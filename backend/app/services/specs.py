"""产品规格与版本管理。

- 别名全局唯一归并；把别名挂到第二个产品即冲突，需先做同名裁定（合并产品）。
- 同一产品的规格版本生效区间不允许交叠；新版本自动把上一版的 effective_to 收口。
- 版本号单调递增，作为“规格裁定与新增投喂并发”时采用哪一版的裁定依据。
"""

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import FeedProduct, FeedProductAlias, FeedProductVersion
from .feeding import calculate_for_record, find_effective_version, parse_feed_text


class SpecError(ValueError):
    """规格登记/裁定业务错误。"""


def get_product(db: Session, product_id: int) -> Optional[FeedProduct]:
    return db.get(FeedProduct, product_id)


def find_product_by_name(db: Session, name: str) -> Optional[FeedProduct]:
    return db.query(FeedProduct).filter(FeedProduct.canonical_name == name).first()


def create_product(db: Session, canonical_name: str, aliases: Optional[List[str]] = None) -> FeedProduct:
    canonical_name = (canonical_name or "").strip()
    if not canonical_name:
        raise SpecError("产品标准名不能为空")
    if find_product_by_name(db, canonical_name):
        raise SpecError(f"产品已存在: {canonical_name}")

    product = FeedProduct(canonical_name=canonical_name, status="active")
    db.add(product)
    db.flush()
    _add_aliases(db, product, [canonical_name] + list(aliases or []))
    db.flush()
    return product


def _add_aliases(db: Session, product: FeedProduct, names: List[str]) -> None:
    seen = set()
    for raw in names:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        exists = db.query(FeedProductAlias).filter(FeedProductAlias.alias == name).first()
        if exists:
            if exists.product_id == product.id:
                continue
            other = db.get(FeedProduct, exists.product_id)
            raise SpecError(
                f"别名 '{name}' 已属于产品 '{other.canonical_name if other else exists.product_id}'；"
                f"若确为同一产品请先做同名裁定合并"
            )
        db.add(FeedProductAlias(product_id=product.id, alias=name))


def add_alias(db: Session, product_id: int, alias: str) -> FeedProductAlias:
    product = get_product(db, product_id)
    if not product:
        raise SpecError("产品不存在")
    _add_aliases(db, product, [alias])
    db.flush()
    return db.query(FeedProductAlias).filter(FeedProductAlias.alias == alias.strip()).first()


def add_version(db: Session, product_id: int, kg_per_bag: float, effective_from: date,
                package_label: Optional[str] = None) -> FeedProductVersion:
    """
    登记包装版本（生效区间按相邻版本自动界定，登记顺序无关）：
    - 新版本 effective_to 自动取“下一个更晚生效版本”的生效日，没有则开放；
    - 更早的开放版本自动收口到新版生效日；
    - 与任一既有版本区间交叠（含同一生效日）即拒绝，不靠猜测裁剪。
    """
    product = get_product(db, product_id)
    if not product:
        raise SpecError("产品不存在")
    if kg_per_bag is None or kg_per_bag <= 0:
        raise SpecError("每袋净重必须为正数")

    versions = sorted(
        db.query(FeedProductVersion).filter(FeedProductVersion.product_id == product_id).all(),
        key=lambda v: v.effective_from,
    )
    earlier = [v for v in versions if v.effective_from < effective_from]
    later = [v for v in versions if v.effective_from > effective_from]
    if any(v.effective_from == effective_from for v in versions):
        raise SpecError(f"产品在 {effective_from} 已存在生效版本, 同一生效日只能有一版")

    # 与更早版本的终点交叠？
    for v in earlier:
        if v.effective_to is None or v.effective_to > effective_from:
            if v.effective_to is None:
                break  # 开放旧版，下面收口
            raise SpecError(
                f"新生效日 {effective_from} 落在版本 v{v.version} "
                f"({v.effective_from}~{v.effective_to}) 区间内"
            )

    next_version = later[0] if later else None
    new_effective_to = next_version.effective_from if next_version else None

    # 更早的开放版本收口到新版生效日
    for v in earlier:
        if v.effective_to is None:
            v.effective_to = effective_from

    max_no = max((v.version for v in versions), default=0)
    version = FeedProductVersion(
        product_id=product_id,
        version=max_no + 1,
        package_label=package_label,
        kg_per_bag=kg_per_bag,
        effective_from=effective_from,
        effective_to=new_effective_to,
    )
    db.add(version)
    db.flush()
    return version


def list_versions(db: Session, product_id: int) -> List[FeedProductVersion]:
    return db.query(FeedProductVersion).filter(
        FeedProductVersion.product_id == product_id
    ).order_by(FeedProductVersion.version).all()


def correct_version(db: Session, version_id: int, *, kg_per_bag: Optional[float] = None,
                    package_label: Optional[str] = None) -> FeedProductVersion:
    """
    包装更正：只允许更正每袋净重/包装标注，不允许改动生效日（生效区间是历史事实）。
    更正本身不改动任何投喂记录；受影响记录由调用方创建 spec_change 回算任务，
    且只重算未签署记录——已签署记录保留签署时的公斤数与规格版本快照。
    """
    version = db.get(FeedProductVersion, version_id)
    if not version:
        raise SpecError("规格版本不存在")
    if kg_per_bag is not None:
        if kg_per_bag <= 0:
            raise SpecError("每袋净重必须为正数")
        version.kg_per_bag = kg_per_bag
    if package_label is not None:
        version.package_label = package_label
    db.flush()
    return version


def adjudicate_same_name(db: Session, source_product_id: int, target_product_id: int,
                         *, recalc_records=True) -> dict:
    """
    同名产品裁定：把 source 合并进 target（别名归并、旧记录改指、source 标记 merged）。
    两个产品必须不同且都处于 active。冲突复核项随受影响记录重算自动消解或继续开放。
    """
    if source_product_id == target_product_id:
        raise SpecError("不能合并产品自身")
    source = get_product(db, source_product_id)
    target = get_product(db, target_product_id)
    if not source or not target:
        raise SpecError("待合并产品不存在")
    if source.status == "merged":
        raise SpecError("来源产品已被合并")
    if target.status == "merged":
        raise SpecError("目标产品已被合并, 请选择有效产品")

    # 别名逐个归并到目标；目标已有的同名词直接丢弃 source 侧重复别名
    relocated_aliases = []
    for alias in db.query(FeedProductAlias).filter(
        FeedProductAlias.product_id == source.id
    ).all():
        clash = db.query(FeedProductAlias).filter(
            FeedProductAlias.alias == alias.alias,
            FeedProductAlias.product_id == target.id,
        ).first()
        if clash:
            db.delete(alias)
        else:
            alias.product_id = target.id
            relocated_aliases.append(alias.alias)

    affected_record_ids = []
    if recalc_records:
        from ..models import FeedingMeasurement, FeedingRecord, ReviewItem

        relocated_set = set(relocated_aliases)
        # 1) 已指向来源产品的未签署记录直接改指并重算
        rows = db.query(FeedingMeasurement, FeedingRecord).join(
            FeedingRecord, FeedingMeasurement.record_id == FeedingRecord.id
        ).filter(
            FeedingMeasurement.product_id == source.id,
            FeedingRecord.signed_off.is_(False),
        ).all()
        for measurement, record in rows:
            measurement.product_id = target.id
            affected_record_ids.append(record.id)
            calculate_for_record(db, record, force=True)

        # 2) 同名冲突/缺产品而挂起、且原文能命中本次归并别名的记录也真正受影响
        open_review_records = db.query(FeedingRecord).join(
            ReviewItem, ReviewItem.record_id == FeedingRecord.id
        ).filter(
            ReviewItem.status == "open",
            FeedingRecord.signed_off.is_(False),
        ).all()
        for record in open_review_records:
            parsed = parse_feed_text(record.feed_type)
            keys = set(parsed["names"]) | ({parsed["lot"]} if parsed["lot"] else set())
            if keys & relocated_set or source.canonical_name in keys:
                affected_record_ids.append(record.id)
                calculate_for_record(db, record, force=True)
    source.status = "merged"
    source.merged_into_id = target.id
    db.flush()
    return {
        "merged_product_id": source.id,
        "target_product_id": target.id,
        "relocated_aliases": len(relocated_aliases),
        "recalculated_records": affected_record_ids,
    }


def version_for_date(db: Session, product_id: int, on_date: date) -> Optional[FeedProductVersion]:
    return find_effective_version(db, product_id, on_date)
