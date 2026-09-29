"""饲料产品与规格版本管理。

- 产品支持全名/简称/别名；规格以"生效版本"管理；
- 供应商更换包装 = 新增版本（包装更正），与受影响区间的重算放在**同一事务**；
- 同名冲突通过"合并裁定"解决；
- 任何更正只重算未签署且真正受影响（投喂日落入生效区间）的记录。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import List

from ..database import get_db
from ..models import FeedProduct, FeedProductVersion
from ..schemas import (
    FeedProductCreate, FeedProductUpdate, FeedProductResponse,
    FeedVersionCreate, FeedVersionResponse,
    PackageCorrectionRequest, FeedProductMergeRequest,
)
from ..services import conversion as conversion_service
from ..services.units import normalize_package_kg, ConversionError

router = APIRouter(
    prefix="/api/feed-products",
    tags=["饲料产品规格"]
)


def _next_version_number(db: Session, product_id: int) -> int:
    last = (
        db.query(FeedProductVersion.version_number)
        .filter(FeedProductVersion.product_id == product_id)
        .order_by(FeedProductVersion.version_number.desc())
        .first()
    )
    return (last[0] + 1) if last else 1


def _validate_range(effective_from, effective_to):
    if effective_to is not None and effective_to <= effective_from:
        raise HTTPException(status_code=400, detail="生效结束日必须晚于生效起始日")


def _add_version(db: Session, product_id: int, package_kg, effective_from,
                 effective_to, change_reason):
    """新增一个生效版本，保证同一产品的生效期互不重叠、衔接完整。

    - 新版本开放（effective_to 为空）：自动把当前开放版本截止到新起始日；
    - 新版本为有限区间且当前存在开放版本：
        · 完全早于开放版本（补录历史）：直接插入；
        · 起点晚于/等于开放起始日：关闭旧版到新起点，插入新区间，
          并自动追加一个沿用旧包装的开放延续版本，保证日后记录仍有规格；
        · 与开放版本交叉：拒绝；
    - 有限区间与有限区间重叠：拒绝。
    返回新版本。是否真正重算某条记录由换算服务的"指纹变化"把关，
    因此这里直接重算该产品全部未签署记录，区间外指纹不变者自动跳过。
    """
    _validate_range(effective_from, effective_to)
    try:
        pkg = normalize_package_kg(package_kg)
    except ConversionError as exc:
        raise HTTPException(status_code=400, detail=exc.message)

    continuation = None
    open_version = (
        db.query(FeedProductVersion)
        .filter(
            FeedProductVersion.product_id == product_id,
            FeedProductVersion.effective_to.is_(None),
        )
        .first()
    )

    finites = (
        db.query(FeedProductVersion)
        .filter(
            FeedProductVersion.product_id == product_id,
            FeedProductVersion.effective_to.is_not(None),
        )
        .all()
    )
    for v in finites:
        if effective_to is not None:
            if effective_from < v.effective_to and v.effective_from < effective_to:
                raise HTTPException(
                    status_code=400, detail=f"与版本 v{v.version_number} 生效期重叠"
                )

    if effective_to is None:
        # 开放新版本：衔接旧开放版本
        if open_version is not None:
            if effective_from <= open_version.effective_from:
                raise HTTPException(
                    status_code=400,
                    detail=f"与当前开放版本 v{open_version.version_number} 生效期重叠；补录历史请给出生效结束日",
                )
            open_version.effective_to = effective_from
    else:
        if open_version is not None:
            os_ = open_version.effective_from
            if effective_to <= os_:
                pass  # 早于开放版本的历史补录，互不相交
            elif effective_from >= os_:
                if effective_from == os_:
                    raise HTTPException(
                        status_code=400,
                        detail=f"开放版本 v{open_version.version_number} 自此日生效；"
                               f"覆盖它请改用不带生效结束日的新版本",
                    )
                # 关闭旧版 -> 插入更正区间 -> 追加沿用旧包装的开放延续版本
                open_version.effective_to = effective_from
                continuation = (open_version.package_kg, effective_to)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"新区间与开放版本 v{open_version.version_number} 交叉，无法裁定",
                )

    version = FeedProductVersion(
        product_id=product_id,
        version_number=_next_version_number(db, product_id),
        package_kg=float(pkg),
        effective_from=effective_from,
        effective_to=effective_to,
        change_reason=change_reason,
    )
    db.add(version)
    db.flush()

    if continuation is not None:
        cont_pkg, cont_from = continuation
        cont = FeedProductVersion(
            product_id=product_id,
            version_number=_next_version_number(db, product_id),
            package_kg=float(cont_pkg),
            effective_from=cont_from,
            effective_to=None,
            change_reason=f"包装更正区间结束，恢复 v{open_version.version_number} 包装",
        )
        db.add(cont)
        db.flush()

    return version


@router.post("/", response_model=FeedProductResponse)
def create_product(payload: FeedProductCreate, db: Session = Depends(get_db)):
    exists = db.query(FeedProduct).filter(FeedProduct.full_name == payload.full_name).first()
    if exists:
        raise HTTPException(status_code=400, detail="产品全名已存在；若是同名不同产品请区分名称后再建")

    product = FeedProduct(
        full_name=payload.full_name,
        short_name=payload.short_name,
        aliases=payload.aliases,
    )
    db.add(product)
    db.flush()

    if payload.package_kg is not None:
        if payload.effective_from is None:
            raise HTTPException(status_code=400, detail="提供袋规格时必须同时提供 effective_from")
        _add_version(
            db, product.id, payload.package_kg, payload.effective_from,
            None, payload.change_reason,
        )
        # 首个生效版本可能正好补齐此前"规格缺失"待复核记录所等待的规格
        conversion_service.recompute_affected(db)

    db.commit()
    db.refresh(product)
    return product

@router.get("/", response_model=List[FeedProductResponse])
def list_products(db: Session = Depends(get_db)):
    return db.query(FeedProduct).order_by(FeedProduct.id.asc()).all()


@router.get("/{product_id}/", response_model=FeedProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(FeedProduct).filter(FeedProduct.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    return product


@router.put("/{product_id}/", response_model=FeedProductResponse)
def update_product(product_id: int, payload: FeedProductUpdate, db: Session = Depends(get_db)):
    product = db.query(FeedProduct).filter(FeedProduct.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(product, key, value)
    flag_modified(product, "aliases")
    db.flush()
    # 别名变化可能让原本无法识别的投喂记录获得规格 —— 全量重算未签署记录
    conversion_service.recompute_affected(db, product_ids=[product_id])
    db.commit()
    db.refresh(product)
    return product


@router.post("/{product_id}/versions/", response_model=FeedVersionResponse)
def add_version(product_id: int, payload: FeedVersionCreate, db: Session = Depends(get_db)):
    product = db.query(FeedProduct).filter(FeedProduct.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    version = _add_version(
        db, product_id, payload.package_kg, payload.effective_from,
        payload.effective_to, payload.change_reason,
    )
    # 新版本/延续版本可能改变任何记录采用的规格 —— 同一事务内立即重算。
    # 只有换算指纹真正变化的记录会被更新（区间外仍命中原版本者自动跳过）。
    conversion_service.recompute_affected(db, product_ids=[product_id])
    db.commit()
    db.refresh(version)
    return version


@router.post("/correct-package/")
def correct_package(payload: PackageCorrectionRequest, db: Session = Depends(get_db)):
    """包装更正（供应商换包装等）：新增版本 + 仅重算未签署且落入区间的记录。"""
    product = db.query(FeedProduct).filter(FeedProduct.id == payload.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    version = _add_version(
        db, payload.product_id, payload.package_kg, payload.effective_from,
        payload.effective_to, payload.change_reason or "包装更正",
    )
    # 只重算未签署且真正受影响（指纹变化）的记录；已签署记录在服务层排除。
    stats = conversion_service.recompute_affected(db, product_ids=[payload.product_id])
    db.commit()
    db.refresh(version)
    return {
        "version_id": version.id,
        "version_number": version.version_number,
        "package_kg": version.package_kg,
        "effective_from": version.effective_from,
        "effective_to": version.effective_to,
        "recomputed": stats,
        "note": "已签署记录未重算",
    }


@router.post("/merge/")
def merge_products(payload: FeedProductMergeRequest, db: Session = Depends(get_db)):
    """同名裁定：把 source 并入 target。区间内未签署引用记录重算到 target。"""
    if payload.source_id == payload.target_id:
        raise HTTPException(status_code=400, detail="不能把产品并入自身")
    source = db.query(FeedProduct).filter(FeedProduct.id == payload.source_id).first()
    target = db.query(FeedProduct).filter(FeedProduct.id == payload.target_id).first()
    if not source or not target:
        raise HTTPException(status_code=404, detail="来源或目标产品不存在")

    source.status = "merged"
    source.merged_into_id = target.id
    db.flush()

    stats = conversion_service.recompute_affected(
        db,
        product_ids=[source.id, target.id],
        date_from=payload.effective_from,
        date_to=payload.date_to,
    )
    db.commit()
    return {
        "merged_product_id": source.id,
        "target_product_id": target.id,
        "recomputed": stats,
        "note": "同名裁定完成；已签署记录保留原版本未重算",
    }
