from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import RecalcJobResponse, RecalcJobCreateRequest
from ..services import recalc as recalc_service
from ..services import specs as spec_service

router = APIRouter(
    prefix="/api/recalc",
    tags=["旧记录分批回算"]
)


def _to_response(snapshot: dict, db: Session) -> RecalcJobResponse:
    return RecalcJobResponse(
        id=snapshot["id"],
        scope=snapshot["scope"],
        params=snapshot["params"],
        status=snapshot["status"],
        total=snapshot["total"],
        processed=snapshot["processed"],
        unchanged=snapshot["unchanged"],
        to_review=snapshot["to_review"],
        skipped_signed=snapshot["skipped_signed"],
        last_id=snapshot["last_id"],
        finished_at=snapshot["finished_at"],
        error=snapshot["error"],
    )


@router.post("/jobs/", response_model=RecalcJobResponse, status_code=201)
def create_job(payload: RecalcJobCreateRequest, db: Session = Depends(get_db)):
    if payload.scope not in ("all", "spec_change"):
        raise HTTPException(status_code=422, detail="scope 只允许 all/spec_change")
    if payload.date_from and payload.date_to and payload.date_to <= payload.date_from:
        raise HTTPException(status_code=422, detail="date_to 必须晚于 date_from（date_to 为不含的上界）")

    params = {}
    if payload.scope == "spec_change":
        if not payload.product_id:
            raise HTTPException(status_code=422, detail="spec_change 必须提供 product_id")
        if not spec_service.get_product(db, payload.product_id):
            raise HTTPException(status_code=404, detail="产品不存在")
        params = {
            "product_id": payload.product_id,
            "date_from": payload.date_from.isoformat() if payload.date_from else None,
            "date_to": payload.date_to.isoformat() if payload.date_to else None,
        }
    try:
        snapshot = recalc_service.create_job(db, payload.scope, params)
    except recalc_service.RecalcError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _to_response(snapshot, db)


@router.post("/jobs/{job_id}/run/", response_model=RecalcJobResponse)
def run_job(job_id: int, limit: int = 100, db: Session = Depends(get_db)):
    """执行一批；重复调用即可断点续跑，直到 status=done。"""
    try:
        snapshot = recalc_service.run_batch(db, job_id, limit=limit)
    except recalc_service.RecalcError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _to_response(snapshot, db)


@router.get("/jobs/{job_id}/", response_model=RecalcJobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    snapshot = recalc_service.get_job(db, job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="回算任务不存在")
    return _to_response(snapshot, db)
