from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional

from ..database import get_db
from ..models import FeedProduct
from ..schemas import (
    FeedProductCreate, FeedProductResponse,
    FeedAliasCreate,
    FeedVersionCreate, FeedVersionResponse, FeedVersionCorrectRequest,
    SameNameAdjudicationRequest, SameNameAdjudicationResponse,
)
from ..services import specs as spec_service

router = APIRouter(
    prefix="/api/feed-products",
    tags=["饲料产品与包装规格"]
)


def _product_to_response(product: FeedProduct) -> FeedProductResponse:
    return FeedProductResponse(
        id=product.id,
        canonical_name=product.canonical_name,
        status=product.status,
        merged_into_id=product.merged_into_id,
        aliases=[a.alias for a in product.aliases],
        created_at=product.created_at,
    )


@router.post("/", response_model=FeedProductResponse, status_code=201)
def create_product(payload: FeedProductCreate, db: Session = Depends(get_db)):
    try:
        product = spec_service.create_product(db, payload.canonical_name, payload.aliases)
    except spec_service.SpecError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    db.refresh(product)
    return _product_to_response(product)


@router.get("/", response_model=List[FeedProductResponse])
def list_products(name: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(FeedProduct)
    if name:
        query = query.filter(FeedProduct.canonical_name.like(f"%{name}%"))
    return [_product_to_response(p) for p in query.order_by(FeedProduct.id).all()]


@router.get("/{product_id}/", response_model=FeedProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = spec_service.get_product(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    return _product_to_response(product)


@router.post("/{product_id}/aliases/", response_model=FeedProductResponse, status_code=201)
def add_alias(product_id: int, payload: FeedAliasCreate, db: Session = Depends(get_db)):
    try:
        spec_service.add_alias(db, product_id, payload.alias)
    except spec_service.SpecError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    product = spec_service.get_product(db, product_id)
    return _product_to_response(product)


@router.get("/{product_id}/versions/", response_model=List[FeedVersionResponse])
def list_versions(product_id: int, db: Session = Depends(get_db)):
    if not spec_service.get_product(db, product_id):
        raise HTTPException(status_code=404, detail="产品不存在")
    return spec_service.list_versions(db, product_id)


@router.post("/{product_id}/versions/", response_model=FeedVersionResponse, status_code=201)
def add_version(product_id: int, payload: FeedVersionCreate, db: Session = Depends(get_db)):
    """
    登记新生效包装版本。版本号单调递增；同一产品生效区间不交叠、同一生效日唯一。
    规格裁定（新版本）与新增投喂并发时，投喂换算始终按投喂日期命中的版本确定采用哪一版，
    先提交的同生效日版本胜出，后提交者收到 409。
    """
    try:
        version = spec_service.add_version(
            db, product_id,
            kg_per_bag=payload.kg_per_bag,
            effective_from=payload.effective_from,
            package_label=payload.package_label,
        )
        db.commit()
    except spec_service.SpecError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同一生效日的规格版本已存在，并发登记以先提交的版本为准")
    db.refresh(version)
    return version


@router.patch("/versions/{version_id}/correct/", response_model=FeedVersionResponse)
def correct_version(version_id: int, payload: FeedVersionCorrectRequest, db: Session = Depends(get_db)):
    """
    包装更正：只能改每袋净重/标注，不能改生效日。更正本身不改任何记录，
    需随后创建 spec_change 回算任务（只影响未签署且落在该版本生效区间的记录）。
    """
    if payload.kg_per_bag is not None and payload.kg_per_bag <= 0:
        raise HTTPException(status_code=422, detail="每袋净重必须为正数")
    try:
        version = spec_service.correct_version(
            db, version_id,
            kg_per_bag=payload.kg_per_bag,
            package_label=payload.package_label,
        )
        db.commit()
    except spec_service.SpecError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    db.refresh(version)
    return version


@router.post("/{product_id}/adjudicate-same-name/", response_model=SameNameAdjudicationResponse)
def adjudicate_same_name(product_id: int, payload: SameNameAdjudicationRequest, db: Session = Depends(get_db)):
    """同名产品裁定：路径产品即合并来源（与 body.source_product_id 必须一致），并入目标产品。"""
    if payload.source_product_id != product_id:
        raise HTTPException(status_code=422, detail="路径产品ID与请求体 source_product_id 不一致")
    try:
        result = spec_service.adjudicate_same_name(
            db, payload.source_product_id, payload.target_product_id
        )
    except spec_service.SpecError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return result
