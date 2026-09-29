"""投喂记录换算流水线。

职责：
- 对单条投喂记录解析产品、裁定生效版本、按规定精度换算成公斤；
- 规格缺失/同名冲突 -> review（待复核隔离），绝不猜测比例对平数字；
- 换算指纹幂等：输入未变的记录不重复换算；
- 旧记录分批回填：可中断、可续跑、只拾取 pending 记录，防止重复换算；
- 包装更正/新版本/产品合并：只重算未签署且"真正受影响"的记录；
- 规格裁定与新增投喂并发：以记录投喂日期落到哪一版生效期为准（版本决定）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    FeedingRecord, ConversionBatch,
    CONVERSION_PENDING, CONVERSION_CONVERTED, CONVERSION_REVIEW,
)
from . import products as product_service
from .units import (
    convert_to_kg, conversion_input_hash, normalize_unit,
    ConversionError,
)

BATCH_RUNNING = "running"
BATCH_INTERRUPTED = "interrupted"
BATCH_COMPLETED = "completed"


def _decimal_to_float(d):
    return float(d) if d is not None else None


def evaluate_record(db: Session, record: FeedingRecord):
    """纯计算：返回 (kg Decimal或None, product, version, status, reason, 采用的袋规格)。不写库。"""
    # 已签署记录不参与任何重算
    resolution = product_service.resolve(
        db, record.feed_type, record.feeding_date,
        override_product_id=record.resolved_product_id,
    )

    unit = normalize_unit(record.unit)
    # 袋计量的每袋公斤数：以记录上"当时包装"为准（现场快照），
    # 当时未记录时回落到规格版本（仍属于按版本裁定，不猜测）。
    package_kg = record.package_kg
    if unit == "bag" and package_kg is None and resolution.status == "converted":
        package_kg = resolution.version.package_kg

    if resolution.status != "converted":
        return None, resolution.product, resolution.version, CONVERSION_REVIEW, resolution.reason, package_kg

    try:
        kg = convert_to_kg(record.quantity, record.unit, package_kg)
    except ConversionError as exc:
        return None, resolution.product, resolution.version, CONVERSION_REVIEW, exc.reason, package_kg
    return kg, resolution.product, resolution.version, CONVERSION_CONVERTED, None, package_kg


def _fingerprint(record, product, version):
    return conversion_input_hash(
        feed_type=record.feed_type,
        quantity=record.quantity,
        unit=record.unit,
        package_kg=record.package_kg,
        product_id=product.id if product else None,
        spec_version_id=version.id if version else None,
    )


def apply_conversion(db: Session, record: FeedingRecord, *,
                     batch_id: Optional[int] = None, force: bool = False) -> str:
    """换算并持久化单条记录。返回新状态。已签署记录直接跳过。

    force=False 时，若换算指纹与上次一致则不重复换算（幂等）。
    """
    if record.signed_at is not None:
        return record.conversion_status or CONVERSION_CONVERTED

    kg, product, version, status, reason, used_package = evaluate_record(db, record)
    fingerprint = _fingerprint(record, product, version)
    if (
        not force
        and record.conversion_status == status
        and record.conversion_hash == fingerprint
        and status == CONVERSION_CONVERTED
    ):
        return status  # 输入未变，无需重复换算

    record.product_id = product.id if product else None
    record.spec_version_id = version.id if version else None
    record.resolved_package_kg = _decimal_to_float(used_package) if normalize_unit(record.unit) == "bag" else None
    record.conversion_status = status
    record.review_reason = reason
    record.conversion_hash = fingerprint
    record.conversion_batch_id = batch_id
    record.converted_at = datetime.utcnow()
    if status == CONVERSION_CONVERTED:
        record.feed_quantity = _decimal_to_float(kg)
    else:
        # 待复核记录不得贡献公斤数到任何汇总
        record.feed_quantity = None
    return status


def convert_one(db: Session, record: FeedingRecord) -> str:
    """新增/修改投喂记录时即时换算（单事务，由调用方提交）。"""
    return apply_conversion(db, record, force=True)


def mark_review_resolved(db: Session, record: FeedingRecord) -> str:
    """复核完成（产品/版本/原始计量已更正）后重新换算。记录须未签署。"""
    if record.signed_at is not None:
        raise ValueError("已签署记录不能在复核流程中修改")
    return apply_conversion(db, record, force=True)


def recompute_affected(db: Session, *, product_ids=None, date_from=None, date_to=None):
    """包装更正/新版本/合并后重算：仅未签署且真正受影响（指纹变化）的记录。

    受影响范围：product_id 命中（或 feed_type 经重新解析会命中）给定产品、
    且投喂日落入 [date_from, date_to] 的记录。已签署记录一律排除。
    返回 {converted, review, skipped_signed}。
    """
    stats = {"converted": 0, "review": 0, "skipped_signed": 0}

    # 重算所有未签署记录（含 pending/review：新增版本可能正好补齐其等待的规格）。
    # 是否真正改写由换算指纹把关；回填只拾取 pending，已在此转换的不会重复换算。
    q = db.query(FeedingRecord).filter(FeedingRecord.signed_at.is_(None))
    records = q.all()
    for record in records:
        if not _in_window(record.feeding_date, date_from, date_to):
            continue
        old_hash = record.conversion_hash
        old_status = record.conversion_status
        kg, product, version, status, reason, used_package = evaluate_record(db, record)

        if product_ids is not None:
            related = (product and product.id in product_ids) or (
                record.product_id in product_ids
            )
            if not related:
                continue

        new_hash = _fingerprint(record, product, version)
        # 真正受影响：解析出的规格/版本/结果发生变化
        affected = (
            new_hash != old_hash
            or record.spec_version_id != (version.id if version else None)
            or old_status != status
        )
        if not affected:
            continue

        record.product_id = product.id if product else None
        record.spec_version_id = version.id if version else None
        record.resolved_package_kg = _decimal_to_float(used_package) if normalize_unit(record.unit) == "bag" else None
        record.conversion_status = status
        record.review_reason = reason
        record.conversion_hash = new_hash
        record.converted_at = datetime.utcnow()
        record.feed_quantity = _decimal_to_float(kg) if status == CONVERSION_CONVERTED else None
        stats[status] = stats.get(status, 0) + 1

    db.flush()
    return stats


def _in_window(day, date_from, date_to):
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def run_backfill(db: Session, *, batch_id=None, limit=200, interrupt=False):
    """分批回填旧记录。

    - 只拾取 conversion_status='pending' 的记录，已处理(converted/review)
      的记录不会被再次拾取（防止重复换算）；
    - 按 id 游标推进，每批最多 limit 条，limit 很小即可模拟分批；
    - interrupt=True 时处理完本批即把批次置为 interrupted，可再次调用续跑；
    - 没有 pending 记录时批次置为 completed。续跑一个已完成批次是安全的空操作。
    """
    if batch_id is None:
        batch = ConversionBatch(status=BATCH_RUNNING, last_record_id=0)
        db.add(batch)
        db.flush()
        batch_id = batch.id
    else:
        batch = db.query(ConversionBatch).filter(ConversionBatch.id == batch_id).first()
        if batch is None:
            raise ValueError(f"换算批次不存在: {batch_id}")

    # 续跑：从中断处继续；拾取条件以状态 pending 为准（游标只决定处理顺序）。
    records = (
        db.query(FeedingRecord)
        .filter(
            FeedingRecord.conversion_status == CONVERSION_PENDING,
            FeedingRecord.id > batch.last_record_id,
        )
        .order_by(FeedingRecord.id.asc())
        .limit(limit)
        .all()
    )
    if not records:
        # 游标之前仍有 pending（如复核被退回待处理）时回扫，避免卡死续跑
        leftovers = db.query(FeedingRecord).filter(
            FeedingRecord.conversion_status == CONVERSION_PENDING
        ).count()
        if leftovers:
            batch.last_record_id = 0
            records = (
                db.query(FeedingRecord)
                .filter(FeedingRecord.conversion_status == CONVERSION_PENDING)
                .order_by(FeedingRecord.id.asc())
                .limit(limit)
                .all()
            )

    processed = 0
    for record in records:
        status = apply_conversion(db, record, batch_id=batch_id)
        batch.total_seen += 1
        if status == CONVERSION_CONVERTED:
            batch.converted_count += 1
        elif status == CONVERSION_REVIEW:
            batch.review_count += 1
        batch.last_record_id = max(batch.last_record_id, record.id)
        processed += 1

    batch.last_run_at = datetime.utcnow()
    db.flush()  # 会话为 autoflush=False，统计前必须落盘本批改写的状态

    # 再扫一遍确认没有遗漏的 pending（含游标之前被退回复核的记录）
    remaining = db.query(FeedingRecord).filter(
        FeedingRecord.conversion_status == CONVERSION_PENDING
    ).count()

    if remaining == 0:
        batch.status = BATCH_COMPLETED
    else:
        # 仍有待处理记录：显式中断或取满一批都保持 interrupted，等待续跑
        batch.status = BATCH_INTERRUPTED

    db.flush()
    return batch
