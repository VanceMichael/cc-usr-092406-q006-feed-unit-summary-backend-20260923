from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..schemas import ReviewItemResponse, ReviewResolveRequest
from ..services import reviews as review_service
from ..services.specs import SpecError

router = APIRouter(
    prefix="/api/feeding-reviews",
    tags=["投喂计量复核"]
)


@router.get("/", response_model=List[ReviewItemResponse])
def list_reviews(status: str = "open", batch_id: Optional[int] = None,
                 reason: Optional[str] = None, db: Session = Depends(get_db)):
    if status not in ("open", "resolved", "rejected", "all"):
        raise HTTPException(status_code=422, detail="status 只允许 open/resolved/rejected/all")
    return review_service.list_review_items(db, status=status, batch_id=batch_id, reason=reason)


@router.post("/{record_id}/resolve/")
def resolve_review(record_id: int, payload: ReviewResolveRequest, db: Session = Depends(get_db)):
    """
    人工复核：只能裁定记录归属的产品，不能填写换算比例。
    换算仍按该产品投喂日生效规格执行；规格缺失时复核项继续保持开放。
    """
    try:
        result = review_service.resolve_record(db, record_id, payload.product_id, payload.note)
    except review_service.ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SpecError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return result
