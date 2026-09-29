from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import (
    Batch, Pond, StockingRecord, FeedingRecord, CostRecord, HarvestSale,
    WaterQualityRecord, MedicationRecord,
    CONVERSION_CONVERTED, CONVERSION_REVIEW,
)
from ..schemas import (
    CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo,
    FeedingSummary, FeedingSummaryByProductItem, FeedingRecordTrace,
)
from ..services.reporting import feeding_line

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)


def _feeding_summary(db: Session, batch_id: int, days_cultured):
    """只采用 converted 公斤；pending/review 隔离计数，绝不并入 0。"""
    records = (
        db.query(FeedingRecord)
        .filter(FeedingRecord.batch_id == batch_id)
        .order_by(FeedingRecord.feeding_date.asc(), FeedingRecord.id.asc())
        .all()
    )

    converted_count = pending_count = review_count = 0
    total_kg = 0.0
    groups = {}  # (product_id, spec_version_id) -> 聚合

    for r in records:
        if r.conversion_status == CONVERSION_CONVERTED:
            converted_count += 1
            total_kg += r.feed_quantity or 0.0
            key = (r.product_id, r.spec_version_id)
            g = groups.setdefault(key, {"kg": 0.0, "count": 0, "record": r})
            g["kg"] += r.feed_quantity or 0.0
            g["count"] += 1
        elif r.conversion_status == CONVERSION_REVIEW:
            review_count += 1
        else:
            pending_count += 1

    items = []
    for (product_id, version_id), g in groups.items():
        sample = g["record"]
        version = sample.spec_version
        product = sample.product
        items.append(FeedingSummaryByProductItem(
            product_id=product_id,
            product_name=product.full_name if product else None,
            spec_version_id=version_id,
            spec_version_number=version.version_number if version else None,
            package_kg=version.package_kg if version else None,
            effective_from=version.effective_from if version else None,
            effective_to=version.effective_to if version else None,
            converted_kg=round(g["kg"], 3),
            record_count=g["count"],
        ))

    avg_daily_feed = 0.0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = round(total_kg / days_cultured, 3)

    return FeedingSummary(
        total_feed_weight=round(total_kg, 3),
        feeding_count=len(records),
        avg_daily_feed=avg_daily_feed,
        converted_count=converted_count,
        pending_count=pending_count,
        review_count=review_count,
        by_product_version=items,
    )


@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    initial_quantity = db.query(func.sum(StockingRecord.quantity)).filter(
        StockingRecord.batch_id == batch.id
    ).scalar() or 0

    harvest_weight = db.query(func.sum(HarvestSale.weight)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0

    # 饲料总量只采用"已按规格版本换算"的公斤；待处理/待复核不参与
    feed_total = db.query(func.sum(FeedingRecord.feed_quantity)).filter(
        FeedingRecord.batch_id == batch.id,
        FeedingRecord.conversion_status == CONVERSION_CONVERTED,
    ).scalar() or 0

    total_cost = db.query(func.sum(CostRecord.amount)).filter(
        CostRecord.batch_id == batch.id
    ).scalar() or 0

    total_revenue = db.query(func.sum(HarvestSale.total_amount)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0

    harvest_date = batch.actual_harvest_date
    days_cultured = None
    if harvest_date:
        days_cultured = (harvest_date - batch.stocking_date).days

    survival_rate = 0
    if initial_quantity > 0 and harvest_weight > 0:
        avg_weight_per_fish = 0.5
        estimated_survival = harvest_weight / avg_weight_per_fish
        survival_rate = (estimated_survival / initial_quantity) * 100

    feed_conversion_ratio = 0
    if harvest_weight > 0 and feed_total > 0:
        feed_conversion_ratio = feed_total / harvest_weight

    yield_per_mu = 0
    if pond and pond.area > 0:
        yield_per_mu = harvest_weight / pond.area

    profit = total_revenue - total_cost

    costs = db.query(
        CostRecord.cost_type,
        func.sum(CostRecord.amount).label('total')
    ).filter(
        CostRecord.batch_id == batch.id
    ).group_by(CostRecord.cost_type).all()

    cost_breakdown = {c.cost_type: c.total for c in costs}

    known_types = ['feed', 'medicine', 'labor', 'electricity']
    other_cost = sum(
        amount for cost_type, amount in cost_breakdown.items()
        if cost_type not in known_types
    )

    cost_summary_dict = {
        "feed_cost": cost_breakdown.get('feed', 0),
        "medicine_cost": cost_breakdown.get('medicine', 0),
        "labor_cost": cost_breakdown.get('labor', 0),
        "electricity_cost": cost_breakdown.get('electricity', 0),
        "other_cost": other_cost,
        "total_cost": total_cost
    }

    feeding_summary = _feeding_summary(db, batch.id, days_cultured)

    return CultureCycleAnalysis(
        batch_number=batch.batch_number,
        pond_name=pond.name if pond else "未知",
        species=batch.species,
        stocking_date=batch.stocking_date,
        harvest_date=harvest_date,
        days_cultured=days_cultured,
        initial_quantity=initial_quantity,
        harvest_weight=harvest_weight,
        survival_rate=round(survival_rate, 2),
        feed_total=round(feed_total, 3),
        feed_conversion_ratio=round(feed_conversion_ratio, 2),
        area=pond.area if pond else 0,
        yield_per_mu=round(yield_per_mu, 2),
        total_cost=total_cost,
        total_revenue=total_revenue,
        profit=profit,
        cost_summary=cost_summary_dict,
        feeding_summary=feeding_summary,
    )

@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    stocking_records = db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch.id
    ).all()

    # 逐笔追溯：原始数量/单位/当时包装 + 采用的规格版本 + 换算公斤
    feeding_records = (
        db.query(FeedingRecord)
        .filter(FeedingRecord.batch_id == batch.id)
        .order_by(FeedingRecord.feeding_date.asc(), FeedingRecord.id.asc())
        .all()
    )

    water_quality_records = db.query(WaterQualityRecord).filter(
        WaterQualityRecord.batch_id == batch.id
    ).all()

    medication_records = db.query(MedicationRecord).filter(
        MedicationRecord.batch_id == batch.id
    ).all()

    cost_records = db.query(CostRecord).filter(
        CostRecord.batch_id == batch.id
    ).all()

    harvest_sales = db.query(HarvestSale).filter(
        HarvestSale.batch_id == batch.id
    ).all()

    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id
        ),
        pond_info=PondInfo(
            name=pond.name if pond else None,
            area=pond.area if pond else None,
            water_depth=pond.water_depth if pond else None
        ),
        stocking_records=[
            {
                "species": r.species,
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if hasattr(r, 'created_at') else None
            } for r in stocking_records
        ],
        feeding_records=[FeedingRecordTrace(**feeding_line(r)) for r in feeding_records],
        water_quality_records=[
            {
                "record_date": r.record_date,
                "water_temperature": r.water_temperature,
                "ph_value": r.ph_value,
                "dissolved_oxygen": r.dissolved_oxygen
            } for r in water_quality_records
        ],
        medication_records=[
            {
                "medication_date": r.medication_date,
                "medication_name": r.drug_name,
                "dosage": r.dosage,
                "unit": r.dosage_unit
            } for r in medication_records
        ],
        cost_records=[
            {
                "cost_date": r.cost_date,
                "cost_type": r.cost_type,
                "amount": r.amount,
                "description": r.description
            } for r in cost_records
        ],
        harvest_sales=[
            {
                "sale_date": r.sale_date,
                "weight": r.weight,
                "unit_price": r.unit_price,
                "total_amount": r.total_amount,
                "buyer": r.buyer
            } for r in harvest_sales
        ]
    )

@router.get("/trace-by-number/{batch_number}/", response_model=BatchTraceability)
def trace_by_batch_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"批次号 {batch_number} 不存在")
    return batch_traceability(batch.id, db)
