from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date

from ..database import get_db
from ..models import (
    Batch, Pond, StockingRecord, FeedingRecord, CostRecord, HarvestSale,
    WaterQualityRecord, MedicationRecord, FeedingMeasurement,
)
from ..schemas import (
    CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo,
    FeedingBreakdownItem, VarianceReport, VarianceLine,
)
from ..services.units import round_kg

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)


def _measured_feed_total(db: Session, batch_id: int) -> float:
    """周期分析唯一采用的饲料公斤口径：status=ok 的换算结果。待复核记录一律不计。"""
    total = db.query(func.sum(FeedingMeasurement.quantity_kg)).join(
        FeedingRecord, FeedingMeasurement.record_id == FeedingRecord.id
    ).filter(
        FeedingRecord.batch_id == batch_id,
        FeedingMeasurement.status == "ok",
    ).scalar()
    return round_kg(total or 0)


def _feeding_breakdown(db: Session, batch_id: int) -> List[FeedingBreakdownItem]:
    """按 原文类型/产品/规格版本 聚合，逐组说明采用的规格版本。"""
    rows = db.query(
        FeedingRecord.feed_type.label("feed_type"),
        FeedingMeasurement.product_id.label("product_id"),
        FeedingMeasurement.resolved_product_name.label("resolved_name"),
        FeedingMeasurement.spec_version_no.label("spec_version_no"),
        FeedingMeasurement.status.label("status"),
        func.sum(FeedingMeasurement.quantity_kg).label("total_kg"),
        func.count(FeedingMeasurement.id).label("cnt"),
    ).join(
        FeedingMeasurement, FeedingMeasurement.record_id == FeedingRecord.id
    ).filter(
        FeedingRecord.batch_id == batch_id
    ).group_by(
        FeedingRecord.feed_type,
        FeedingMeasurement.product_id,
        FeedingMeasurement.resolved_product_name,
        FeedingMeasurement.spec_version_no,
        FeedingMeasurement.status,
    ).all()

    items = {}
    for r in rows:
        key = (r.feed_type, r.product_id, r.spec_version_no, r.status)
        item = items.get(key)
        if item is None:
            item = FeedingBreakdownItem(
                feed_type=r.feed_type,
                product_id=r.product_id,
                product_name=r.resolved_name,
                spec_version_no=r.spec_version_no,
                total_kg=0.0,
                feeding_count=0,
                review_count=0,
            )
            items[key] = item
        item.feeding_count += r.cnt
        if r.status == "ok":
            item.total_kg = round_kg((item.total_kg or 0) + (r.total_kg or 0))
        else:
            item.review_count += r.cnt
    return list(items.values())


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

    feed_total = _measured_feed_total(db, batch.id)

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

    feeding_record_count = db.query(func.count(FeedingRecord.id)).filter(
        FeedingRecord.batch_id == batch.id
    ).scalar() or 0
    review_pending_count = db.query(func.count(FeedingRecord.id)).outerjoin(
        FeedingMeasurement, FeedingMeasurement.record_id == FeedingRecord.id
    ).filter(
        FeedingRecord.batch_id == batch.id,
        (FeedingMeasurement.status != "ok") | (FeedingMeasurement.id.is_(None)),
    ).scalar() or 0

    breakdown = _feeding_breakdown(db, batch.id)
    avg_daily_feed = 0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = round_kg(feed_total / days_cultured)

    feeding_summary_result = {
        "total_feed_weight": feed_total,
        "feeding_count": feeding_record_count,
        "avg_daily_feed": avg_daily_feed,
        "measured_only": True,
        "review_pending_count": review_pending_count,
    }

    measurement_note = None
    if review_pending_count:
        measurement_note = (
            f"有 {review_pending_count} 条投喂记录待复核，未计入饲料用量；"
            f"饲料合计仅包含按生效规格换算成功的记录"
        )

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
        feed_total=feed_total,
        feed_conversion_ratio=round(feed_conversion_ratio, 2),
        area=pond.area if pond else 0,
        yield_per_mu=round(yield_per_mu, 2),
        total_cost=total_cost,
        total_revenue=total_revenue,
        profit=profit,
        cost_summary=cost_summary_dict,
        feeding_summary=feeding_summary_result,
        feeding_breakdown=breakdown,
        review_pending_count=review_pending_count,
        feeding_record_count=feeding_record_count,
        measurement_note=measurement_note,
    )


def _feeding_trace_item(r: FeedingRecord):
    m = r.measurement
    kg_per_bag = m.spec_kg_per_bag if m else None
    product_name = None
    if m:
        product_name = m.resolved_product_name
    return {
        "feeding_date": r.feeding_date,
        "feed_type": r.feed_type,
        "quantity": m.quantity_kg if (m and m.status == "ok") else None,
        "unit": "kg" if (m and m.status == "ok") else r.raw_unit,
        "raw_quantity": r.raw_quantity,
        "raw_unit": r.raw_unit,
        "package_label": r.package_label,
        "quantity_kg": m.quantity_kg if (m and m.status == "ok") else None,
        "measurement_status": m.status if m else "review",
        "review_reason": m.review_reason if m else "not_measured",
        "product_id": m.product_id if m else None,
        "product_name": product_name,
        "spec_version_id": m.spec_version_id if m else None,
        "spec_version_no": m.spec_version_no if m else None,
        "spec_effective_from": m.spec_effective_from if m else None,
        "spec_kg_per_bag": kg_per_bag,
        "signed_off": bool(r.signed_off),
    }


@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    stocking_records = db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch.id
    ).all()

    feeding_records = db.query(FeedingRecord).filter(
        FeedingRecord.batch_id == batch.id
    ).order_by(FeedingRecord.feeding_date, FeedingRecord.id).all()

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
        feeding_records=[_feeding_trace_item(r) for r in feeding_records],
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


@router.get("/feeding-variance/", response_model=VarianceReport)
def feeding_variance(
    batch_id: Optional[int] = None,
    product_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """
    月底盘点差额：旧口径（把所有数值当公斤直接累加）对比计量链口径。
    每一行都可经 record_id 回到原始投喂记录；待复核记录单列、不计入 measured_total。
    """
    query = db.query(FeedingRecord)
    if batch_id:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    if product_id:
        query = query.join(
            FeedingMeasurement, FeedingMeasurement.record_id == FeedingRecord.id
        ).filter(FeedingMeasurement.product_id == product_id)
    if date_from:
        query = query.filter(FeedingRecord.feeding_date >= date_from)
    if date_to:
        query = query.filter(FeedingRecord.feeding_date < date_to)

    records = query.order_by(FeedingRecord.feeding_date, FeedingRecord.id).all()

    lines = []
    legacy_total = 0.0
    measured_total = 0.0
    excluded = 0
    for r in records:
        # 旧口径：有旧字段用旧字段；否则模拟旧汇总直接把数值字段当公斤
        legacy_kg = r.feed_quantity
        if legacy_kg is None and r.raw_quantity is not None:
            legacy_kg = r.raw_quantity
        m = r.measurement
        measured_kg = m.quantity_kg if (m and m.status == "ok") else None
        if legacy_kg is not None:
            legacy_total += legacy_kg
        if measured_kg is not None:
            measured_total += measured_kg
        else:
            excluded += 1
        diff = round_kg((measured_kg or 0) - (legacy_kg or 0))
        lines.append(VarianceLine(
            record_id=r.id,
            batch_id=r.batch_id,
            feeding_date=r.feeding_date,
            feed_type=r.feed_type,
            raw_quantity=r.raw_quantity,
            raw_unit=r.raw_unit,
            package_label=r.package_label,
            legacy_kg=legacy_kg,
            measured_kg=measured_kg,
            diff_kg=diff,
            status=m.status if m else "not_measured",
            review_reason=m.review_reason if m else "not_measured",
            spec_version_no=m.spec_version_no if m else None,
            signed_off=bool(r.signed_off),
        ))

    return VarianceReport(
        batch_id=batch_id,
        product_id=product_id,
        date_from=date_from,
        date_to=date_to,
        lines=lines,
        legacy_total_kg=round_kg(legacy_total),
        measured_total_kg=round_kg(measured_total),
        excluded_review_count=excluded,
        variance_kg=round_kg(measured_total - legacy_total),
        note="差额=计量链口径(仅换算成功记录)-旧口径(全部按公斤)；待复核记录不计入计量链口径，需经复核队列处理",
    )
