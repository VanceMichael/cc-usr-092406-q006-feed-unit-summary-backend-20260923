"""盘点对账：待复核隔离、差额核对、旧记录分批回填。"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date
from typing import List, Optional

from ..database import get_db
from ..models import (
    FeedingRecord, ConversionBatch,
    CONVERSION_PENDING, CONVERSION_CONVERTED, CONVERSION_REVIEW,
)
from ..schemas import (
    VarianceReport, VarianceRecordItem, FeedingRecordResponse,
    BackfillRequest, BackfillResult, ConversionBatchResponse,
)
from ..services import conversion as conversion_service
from ..services.reporting import feeding_line
from ..services.units import normalize_unit, quantize_kg

router = APIRouter(
    prefix="/api/reconciliation",
    tags=["盘点对账"]
)


@router.get("/review/", response_model=List[FeedingRecordResponse])
def list_review(batch_id: Optional[int] = None, reason: Optional[str] = None,
                db: Session = Depends(get_db)):
    """待复核记录清单（与正式汇总隔离）。"""
    query = db.query(FeedingRecord).filter(
        FeedingRecord.conversion_status == CONVERSION_REVIEW
    )
    if batch_id is not None:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    if reason:
        query = query.filter(FeedingRecord.review_reason == reason)
    return query.order_by(FeedingRecord.id.asc()).all()


@router.get("/variance/", response_model=VarianceReport)
def variance_report(
    product_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    batch_id: Optional[int] = None,
    # 饲料仓按袋核出的出库量
    warehouse_bags: Optional[float] = None,
    warehouse_package_kg: Optional[float] = None,
    warehouse_kg: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """饲料仓出库量 vs 养殖分析公斤数差额。

    - 养殖分析口径只统计 converted 公斤；pending/review 隔离列出、不进合计；
    - 每条明细可回到原投喂记录（record_id + 原始数量/单位/当时包装/规格版本）；
    - 仓库口径可直接给 warehouse_kg，或给 warehouse_bags × warehouse_package_kg；
    - 绝不通过猜测比例对平数字。
    """
    query = db.query(FeedingRecord)
    if product_id is not None:
        query = query.filter(FeedingRecord.product_id == product_id)
    if batch_id is not None:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    records = query.order_by(FeedingRecord.id.asc()).all()

    converted, pending, review = [], [], []
    converted_kg = Decimal("0")
    bags_consumed = Decimal("0")
    for r in records:
        if date_from and r.feeding_date < date_from:
            continue
        if date_to and r.feeding_date > date_to:
            continue
        line = VarianceRecordItem(**feeding_line(r))
        if r.conversion_status == CONVERSION_CONVERTED:
            converted.append(line)
            converted_kg += Decimal(str(r.feed_quantity))
            if normalize_unit(r.unit) == "bag":
                bags_consumed += Decimal(str(r.quantity))
        elif r.conversion_status == CONVERSION_REVIEW:
            review.append(line)
        else:
            pending.append(line)

    wh_kg = None
    if warehouse_kg is not None:
        wh_kg = quantize_kg(Decimal(str(warehouse_kg)))
    elif warehouse_bags is not None and warehouse_package_kg is not None:
        wh_kg = quantize_kg(
            Decimal(str(warehouse_bags)) * Decimal(str(warehouse_package_kg))
        )
    elif warehouse_bags is not None or warehouse_package_kg is not None:
        raise HTTPException(
            status_code=400,
            detail="按袋核出库量必须同时提供 warehouse_bags 与 warehouse_package_kg",
        )

    variance = quantize_kg(wh_kg - converted_kg) if wh_kg is not None else None

    return VarianceReport(
        product_id=product_id,
        date_from=date_from,
        date_to=date_to,
        warehouse_bags=warehouse_bags,
        warehouse_package_kg=warehouse_package_kg,
        warehouse_kg=float(wh_kg) if wh_kg is not None else None,
        converted_kg=float(converted_kg),
        converted_count=len(converted),
        bags_consumed=float(bags_consumed),
        variance_kg=float(variance) if variance is not None else None,
        pending_records=pending,
        review_records=review,
        converted_records=converted,
    )


@router.post("/backfill/", response_model=BackfillResult)
def backfill(payload: BackfillRequest, batch_id: Optional[int] = None,
             db: Session = Depends(get_db)):
    """旧记录分批换算。可反复调用续跑：每次最多处理 limit 条；

    - interrupt=true 时本批处理完即置为 interrupted，可稍后续跑；
    - 只拾取 pending 记录，已换算/已复核的不会被重复处理；
    - 不带 batch_id 开新批次；带已完成批次续跑是安全空操作。
    """
    before = db.query(FeedingRecord).filter(
        FeedingRecord.conversion_status == CONVERSION_PENDING
    ).count()
    batch = conversion_service.run_backfill(
        db, batch_id=batch_id, limit=payload.limit, interrupt=payload.interrupt
    )
    db.commit()
    db.refresh(batch)
    remaining = db.query(FeedingRecord).filter(
        FeedingRecord.conversion_status == CONVERSION_PENDING
    ).count()
    processed = max(0, before - remaining)
    return BackfillResult(
        batch=ConversionBatchResponse(
            id=batch.id, status=batch.status, total_seen=batch.total_seen,
            converted_count=batch.converted_count, review_count=batch.review_count,
            last_record_id=batch.last_record_id, last_run_at=batch.last_run_at,
            created_at=batch.created_at,
        ),
        processed_in_run=processed,
        pending_remaining=remaining,
    )


@router.get("/batches/", response_model=List[ConversionBatchResponse])
def list_batches(db: Session = Depends(get_db)):
    return db.query(ConversionBatch).order_by(ConversionBatch.id.desc()).all()


@router.get("/batches/{batch_id}/", response_model=ConversionBatchResponse)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(ConversionBatch).filter(ConversionBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="换算批次不存在")
    return batch
