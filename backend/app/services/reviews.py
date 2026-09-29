"""复核处理：规格缺失/同名冲突的人工裁定入口。

人工只能裁定“这条记录归哪个产品”，换算仍严格走该产品投喂日生效规格，
系统不接受人工填写的换算比例。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import FeedProduct, FeedProductAlias, FeedingRecord, ReviewItem
from .feeding import calculate_for_record, parse_feed_text
from .specs import SpecError, get_product


class ReviewError(ValueError):
    pass


def list_review_items(db: Session, status: str = "open", batch_id: Optional[int] = None,
                      reason: Optional[str] = None):
    query = db.query(ReviewItem)
    if status != "all":
        query = query.filter(ReviewItem.status == status)
    if reason:
        query = query.filter(ReviewItem.reason == reason)
    if batch_id:
        query = query.join(FeedingRecord, ReviewItem.record_id == FeedingRecord.id).filter(
            FeedingRecord.batch_id == batch_id
        )
    return query.order_by(ReviewItem.id).all()


def resolve_record(db: Session, record_id: int, product_id: int, note: Optional[str] = None) -> dict:
    record = db.get(FeedingRecord, record_id)
    if not record:
        raise ReviewError("投喂记录不存在")
    if record.signed_off:
        raise ReviewError("记录已签署锁定，不能再复核改判")
    target = get_product(db, product_id)
    if not target:
        raise ReviewError("指定产品不存在")
    if target.status == "merged":
        raise ReviewError("指定产品已被合并，请选择合并后的有效产品")

    # 归并别名：原文中能归并的键挂到目标产品；已属其他产品的键必须先走同名裁定
    parsed = parse_feed_text(record.feed_type)
    keys = list(parsed["names"]) + ([parsed["lot"]] if parsed["lot"] else [])
    registered, blocked = [], []
    for key in keys:
        key = (key or "").strip()
        if not key:
            continue
        existing = db.query(FeedProductAlias).filter(FeedProductAlias.alias == key).first()
        if existing and existing.product_id == target.id:
            continue
        if existing:
            owner = db.get(FeedProduct, existing.product_id)
            blocked.append({"key": key, "owner_product_id": existing.product_id,
                            "owner": owner.canonical_name if owner else None})
            continue
        db.add(FeedProductAlias(product_id=target.id, alias=key))
        registered.append(key)
    db.flush()
    if blocked:
        # 不落库任何半完成归并：交给调用方决定是否先做同名裁定
        db.rollback()
        raise SpecError(f"存在归属其他产品的名称，需先做同名裁定: {blocked}")

    measurement, changed = calculate_for_record(db, record, force=True)
    if measurement.status != "ok":
        # 产品归并成功但投喂日期仍缺生效规格：复核项保持开放
        return {
            "record_id": record_id,
            "status": "review",
            "review_reason": measurement.review_reason,
            "registered_aliases": registered,
            "message": "产品已归并，但该投喂日期缺少生效包装规格，登记规格后复核将自动消解",
        }

    item = db.query(ReviewItem).filter(
        ReviewItem.record_id == record_id, ReviewItem.status == "open"
    ).first()
    if item:
        item.status = "resolved"
        item.resolved_at = datetime.utcnow()
        item.resolution_note = note or f"人工归并到产品 {target.canonical_name}"
    return {
        "record_id": record_id,
        "status": "ok",
        "product_id": target.id,
        "product_name": target.canonical_name,
        "quantity_kg": measurement.quantity_kg,
        "spec_version_no": measurement.spec_version_no,
        "registered_aliases": registered,
    }
