from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional

from ..database import get_db
from ..models import FeedingRecord, Batch, FeedingMeasurement
from ..schemas import FeedingRecordCreate, FeedingRecordUpdate, FeedingRecordResponse, MeasurementBrief
from ..services.feeding import calculate_for_record
from ..services.units import normalize_unit

router = APIRouter(
    prefix="/api/feeding-records",
    tags=["投喂记录"]
)

# 签署后锁定的字段：这些字段变化会影响计量结果
_LOCKED_FIELDS = ("feeding_date", "feed_type", "raw_quantity", "raw_unit", "package_label", "feed_quantity")


def _apply_quantity_fields(record, data: dict) -> None:
    """把入参落到记录：新口径优先；只给旧字段 feed_quantity 时按公斤受理。"""
    raw_qty = data.get("raw_quantity", None)
    legacy_qty = data.get("feed_quantity", None)
    if raw_qty is None and legacy_qty is not None:
        record.raw_quantity = legacy_qty
        record.raw_unit = data.get("raw_unit") or "kg"
    else:
        if "raw_quantity" in data:
            record.raw_quantity = raw_qty
        if "raw_unit" in data:
            record.raw_unit = data.get("raw_unit")
    if "package_label" in data:
        record.package_label = data.get("package_label")
    if "feed_quantity" in data:
        record.feed_quantity = legacy_qty


def _build_response(record: FeedingRecord) -> FeedingRecordResponse:
    m = record.measurement
    brief = None
    if m:
        product_name = None
        if m.product_id:
            product = record.measurement.product
            product_name = product.canonical_name if product else m.resolved_product_name
        else:
            product_name = m.resolved_product_name
        brief = MeasurementBrief(
            status=m.status,
            quantity_kg=m.quantity_kg,
            review_reason=m.review_reason,
            product_id=m.product_id,
            product_name=product_name,
            spec_version_id=m.spec_version_id,
            spec_version_no=m.spec_version_no,
            spec_effective_from=m.spec_effective_from,
            package_label=m.package_label,
            calculated_at=m.calculated_at,
        )
    return FeedingRecordResponse(
        id=record.id,
        batch_id=record.batch_id,
        feeding_date=record.feeding_date,
        feed_type=record.feed_type,
        feed_quantity=record.feed_quantity if record.feed_quantity is not None else (
            m.quantity_kg if (m and m.status == "ok") else None
        ),
        raw_quantity=record.raw_quantity,
        raw_unit=record.raw_unit,
        package_label=record.package_label,
        signed_off=record.signed_off,
        feeding_time=record.feeding_time,
        weather=record.weather,
        water_temperature=record.water_temperature,
        notes=record.notes,
        created_at=record.created_at,
        measurement=brief,
    )


@router.post("/", response_model=FeedingRecordResponse, status_code=201)
def create_feeding_record(record: FeedingRecordCreate, db: Session = Depends(get_db)):
    db_batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    data = record.model_dump(exclude_unset=True)
    raw_quantity = data.get("raw_quantity")
    raw_unit = data.get("raw_unit")
    legacy = data.get("feed_quantity")
    if raw_quantity is None and legacy is None:
        raise HTTPException(status_code=422, detail="必须提供原始数量 raw_quantity(及 raw_unit)，或旧口径 feed_quantity(按公斤)")
    if raw_quantity is not None and not raw_unit:
        raise HTTPException(status_code=422, detail="提供 raw_quantity 时必须同时提供 raw_unit: bag/g/kg")
    if raw_unit and normalize_unit(raw_unit) is None:
        raise HTTPException(status_code=422, detail=f"无法识别的原始单位: {raw_unit}（允许 bag/g/kg 及常见中文写法）")

    new_record = FeedingRecord(
        batch_id=data["batch_id"],
        feeding_date=data["feeding_date"],
        feed_type=data["feed_type"],
        feeding_time=data.get("feeding_time"),
        weather=data.get("weather"),
        water_temperature=data.get("water_temperature"),
        notes=data.get("notes"),
        signed_off=False,
    )
    _apply_quantity_fields(new_record, data)
    # 兼容旧字段：公斤口径同时落到 feed_quantity
    if new_record.raw_unit == "kg":
        new_record.feed_quantity = new_record.raw_quantity

    db.add(new_record)
    db.flush()
    calculate_for_record(db, new_record)
    db.commit()
    db.refresh(new_record)
    return _build_response(new_record)


@router.get("/", response_model=List[FeedingRecordResponse])
def get_feeding_records(skip: int = 0, limit: int = 100, batch_id: int = None,
                        review_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(FeedingRecord)
    if batch_id:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    if review_only:
        query = query.join(
            FeedingMeasurement, FeedingMeasurement.record_id == FeedingRecord.id
        ).filter(FeedingMeasurement.status == "review")
    records = query.order_by(FeedingRecord.id).offset(skip).limit(limit).all()
    return [_build_response(r) for r in records]


@router.get("/{record_id}/", response_model=FeedingRecordResponse)
def get_feeding_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    return _build_response(record)


@router.put("/{record_id}/", response_model=FeedingRecordResponse)
def update_feeding_record(record_id: int, record: FeedingRecordUpdate, db: Session = Depends(get_db)):
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if db_record.signed_off:
        locked = [k for k in _LOCKED_FIELDS if k in record.model_dump(exclude_unset=True)]
        if locked:
            raise HTTPException(
                status_code=409,
                detail=f"记录已签署锁定，不能修改计量相关字段: {', '.join(locked)}；包装更正只重算未签署记录"
            )

    update_data = record.model_dump(exclude_unset=True)
    if "raw_unit" in update_data and update_data["raw_unit"] is not None and normalize_unit(update_data["raw_unit"]) is None:
        raise HTTPException(status_code=422, detail=f"无法识别的原始单位: {update_data['raw_unit']}")

    for key, value in update_data.items():
        if key in ("raw_quantity", "raw_unit", "package_label", "feed_quantity"):
            continue
        setattr(db_record, key, value)
    _apply_quantity_fields(db_record, update_data)
    if db_record.raw_unit == "kg":
        db_record.feed_quantity = db_record.raw_quantity

    calculate_for_record(db, db_record)
    db.commit()
    db.refresh(db_record)
    return _build_response(db_record)


@router.delete("/{record_id}/")
def delete_feeding_record(record_id: int, db: Session = Depends(get_db)):
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if db_record.signed_off:
        raise HTTPException(status_code=409, detail="记录已签署锁定，不能删除")
    db.delete(db_record)
    db.commit()
    return {"message": "投喂记录删除成功"}


@router.post("/{record_id}/sign-off/", response_model=FeedingRecordResponse)
def sign_off_feeding_record(record_id: int, db: Session = Depends(get_db)):
    """签署锁定：签署前必须已成功换算（待复核记录不得签署）。"""
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    m = db_record.measurement
    if not m or m.status != "ok":
        raise HTTPException(status_code=409, detail="记录尚待复核，不能签署；请先完成规格裁定与复核")
    db_record.signed_off = True
    db_record.signed_off_at = datetime.utcnow()
    db.commit()
    db.refresh(db_record)
    return _build_response(db_record)
