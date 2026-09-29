from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from ..database import get_db
from ..models import FeedingRecord, Batch, CONVERSION_CONVERTED, CONVERSION_REVIEW
from ..schemas import (
    FeedingRecordCreate, FeedingRecordUpdate, FeedingRecordResponse,
    SignRequest, SignResult, ReviewResolveRequest,
)
from ..services import conversion as conversion_service
from ..services import products as product_service
from ..services.units import normalize_unit

router = APIRouter(
    prefix="/api/feeding-records",
    tags=["投喂记录"]
)

# 已签署后仍允许改动的非计量字段
_NON_MEASUREMENT_FIELDS = {"weather", "water_temperature", "feeding_time", "notes"}


def _build_record(payload) -> FeedingRecord:
    data = payload.dict()
    # 现场未单独给批号时，尝试从混写的饲料类型文本中剥离批号
    if not data.get("package_batch_no"):
        _, lot = product_service.split_name_and_lot(data.get("feed_type"))
        if lot:
            data["package_batch_no"] = lot
    return FeedingRecord(**data)


@router.post("/", response_model=FeedingRecordResponse)
def create_feeding_record(record: FeedingRecordCreate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    # 规范化单位但不拒绝：无法识别/缺规格的记录照样落库，进入复核而非被丢弃
    new_record = _build_record(record)
    if normalize_unit(record.unit) is not None:
        new_record.unit = normalize_unit(record.unit)
    db.add(new_record)
    db.flush()
    conversion_service.convert_one(db, new_record)
    db.commit()
    db.refresh(new_record)
    return new_record


@router.get("/", response_model=List[FeedingRecordResponse])
def get_feeding_records(skip: int = 0, limit: int = 100, batch_id: int = None,
                        conversion_status: str = None, db: Session = Depends(get_db)):
    query = db.query(FeedingRecord)
    if batch_id:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    if conversion_status:
        query = query.filter(FeedingRecord.conversion_status == conversion_status)
    records = query.order_by(FeedingRecord.id.asc()).offset(skip).limit(limit).all()
    return records


@router.get("/{record_id}/", response_model=FeedingRecordResponse)
def get_feeding_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    return record


@router.put("/{record_id}/", response_model=FeedingRecordResponse)
def update_feeding_record(record_id: int, payload: FeedingRecordUpdate, db: Session = Depends(get_db)):
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")

    update_data = payload.dict(exclude_unset=True)
    signed = db_record.signed_at is not None
    if signed:
        blocked = [k for k in update_data if k not in _NON_MEASUREMENT_FIELDS]
        if blocked:
            # 已签署周期的计量链冻结：包装更正不得借修改记录重算已签署数据
            raise HTTPException(
                status_code=409,
                detail=f"记录已签署，计量字段 {blocked} 不可修改；如需更正请走产品规格版本流程",
            )

    for key, value in update_data.items():
        setattr(db_record, key, value)

    if not signed:
        if "unit" in update_data and normalize_unit(update_data["unit"]) is not None:
            db_record.unit = normalize_unit(update_data["unit"])
        if not db_record.package_batch_no and (
            "feed_type" in update_data or "package_batch_no" in update_data
        ):
            _, lot = product_service.split_name_and_lot(db_record.feed_type)
            if lot:
                db_record.package_batch_no = lot
        # 重新换算：按记录投喂日此刻生效的规格版本决定采用哪一版
        conversion_service.convert_one(db, db_record)

    db.commit()
    db.refresh(db_record)
    return db_record


@router.delete("/{record_id}/")
def delete_feeding_record(record_id: int, db: Session = Depends(get_db)):
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if db_record.signed_at is not None:
        raise HTTPException(status_code=409, detail="记录已签署，不能删除")

    db.delete(db_record)
    db.commit()
    return {"message": "投喂记录删除成功"}


@router.post("/sign/", response_model=SignResult)
def sign_feeding_records(payload: SignRequest, batch_id: int = None,
                         db: Session = Depends(get_db)):
    """签署周期数据。只有已换算成功(converted)的记录可签署；
    pending/review 被隔离跳过。签署后任何规格更正都不会再重算这些记录。"""
    query = db.query(FeedingRecord).filter(FeedingRecord.signed_at.is_(None))
    if payload.record_ids:
        query = query.filter(FeedingRecord.id.in_(payload.record_ids))
    if batch_id is not None:
        query = query.filter(FeedingRecord.batch_id == batch_id)

    signed = 0
    skipped = 0
    now = datetime.utcnow()
    for record in query.all():
        if record.conversion_status == CONVERSION_CONVERTED:
            record.signed_at = now
            signed += 1
        else:
            skipped += 1
    db.commit()
    return SignResult(signed=signed, skipped_unsigned_or_review=skipped)


@router.post("/{record_id}/resolve-review/", response_model=FeedingRecordResponse)
def resolve_review(record_id: int, payload: ReviewResolveRequest,
                   db: Session = Depends(get_db)):
    """人工复核处理：可显式裁定产品、修正原始数量/单位/当时包装，随后重新换算。

    系统不替用户猜测袋比例；所有裁定必须在此显式给出。
    """
    record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if record.signed_at is not None:
        raise HTTPException(status_code=409, detail="记录已签署，不能再进行复核处理")
    if record.conversion_status != CONVERSION_REVIEW:
        raise HTTPException(status_code=400, detail="该记录不处于待复核状态")

    data = payload.dict(exclude_unset=True)
    if "product_id" in data:
        # None 表示撤销显式裁定，恢复按名称解析
        record.resolved_product_id = data["product_id"]
    for key in ("quantity", "package_kg", "package_batch_no", "feed_type", "feeding_date"):
        if key in data:
            setattr(record, key, data[key])
    if "unit" in data and data["unit"] is not None:
        normalized = normalize_unit(data["unit"])
        if normalized is None:
            raise HTTPException(status_code=400, detail=f"不支持的单位: {data['unit']!r}")
        record.unit = normalized

    status = conversion_service.mark_review_resolved(db, record)
    db.commit()
    db.refresh(record)
    if status == CONVERSION_REVIEW:
        # 仍无法换算（例如裁定的产品在投喂日没有生效规格）：保持复核隔离
        raise HTTPException(
            status_code=422,
            detail={
                "message": "复核后仍无法换算，记录保持待复核",
                "review_reason": record.review_reason,
            },
        )
    return record
