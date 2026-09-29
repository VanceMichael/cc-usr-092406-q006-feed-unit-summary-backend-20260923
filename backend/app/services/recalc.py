"""旧记录分批回算。

职责边界：
- 包装/规格更正只重算“未签署”且“真正受影响区间”内的记录；已签署记录锁定不动。
- 分批执行，last_id 为续跑水位；进程重启后凭 DB 中的任务快照续跑（崩溃任务心跳过期可接管）。
- 双层防重复换算：水位保证不重复扫描，FeedingMeasurement.calc_token 保证记录指纹未变不重复换算。
"""

import json
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    FeedProductVersion,
    FeedingMeasurement,
    FeedingRecord,
    RecalcJob,
)
from .feeding import calculate_for_record

HEARTBEAT_TIMEOUT = timedelta(seconds=30)


class RecalcError(ValueError):
    pass


def _base_query(db: Session, scope: str, params: dict):
    query = db.query(FeedingRecord)
    if scope == "all":
        return query
    if scope == "spec_change":
        product_id = params.get("product_id")
        if not product_id:
            raise RecalcError("spec_change 任务必须提供 product_id")
        query = query.join(
            FeedingMeasurement, FeedingMeasurement.record_id == FeedingRecord.id
        ).filter(FeedingMeasurement.product_id == product_id)
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        if date_from:
            query = query.filter(FeedingRecord.feeding_date >= date.fromisoformat(date_from))
        if date_to:
            query = query.filter(FeedingRecord.feeding_date < date.fromisoformat(date_to))
        return query
    raise RecalcError(f"未知回算范围: {scope}")


def create_job(db: Session, scope: str = "all", params: Optional[dict] = None) -> RecalcJob:
    params = params or {}
    base = _base_query(db, scope, params)
    total = base.count()
    job = RecalcJob(
        scope=scope,
        params_json=json.dumps(params, ensure_ascii=False) if params else None,
        status="pending",
        total=total,
        processed=0,
        unchanged=0,
        to_review=0,
        skipped_signed=0,
        last_id=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_snapshot(job)


def _job_snapshot(job: RecalcJob) -> dict:
    return {
        "id": job.id,
        "scope": job.scope,
        "params": json.loads(job.params_json) if job.params_json else {},
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "unchanged": job.unchanged,
        "to_review": job.to_review,
        "skipped_signed": job.skipped_signed,
        "last_id": job.last_id,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
    }


def get_job(db: Session, job_id: int) -> Optional[dict]:
    job = db.get(RecalcJob, job_id)
    return _job_snapshot(job) if job else None


def run_batch(db: Session, job_id: int, limit: int = 100) -> dict:
    """跑一批（至多 limit 条）。可反复调用直到 status=done；中断后凭水位续跑。"""
    if limit <= 0:
        raise RecalcError("limit 必须为正整数")

    job = db.get(RecalcJob, job_id)
    if not job:
        raise RecalcError("回算任务不存在")
    if job.status == "done":
        return _job_snapshot(job)
    if job.status == "failed":
        raise RecalcError(f"任务已失败: {job.error}")
    if job.status == "running" and job.heartbeat_at:
        if datetime.utcnow() - job.heartbeat_at < HEARTBEAT_TIMEOUT:
            raise RecalcError("该任务已有一批正在执行, 请稍后再续跑")
        # 心跳过期 = 上一进程中断，接管续跑

    params = json.loads(job.params_json) if job.params_json else {}
    job.status = "running"
    job.heartbeat_at = datetime.utcnow()
    db.commit()

    try:
        rows = _base_query(db, job.scope, params).filter(
            FeedingRecord.id > job.last_id
        ).order_by(FeedingRecord.id).limit(limit).all()

        for record in rows:
            if record.signed_off:
                job.skipped_signed += 1
            else:
                _, changed = calculate_for_record(db, record)
                if changed:
                    job.processed += 1
                else:
                    job.unchanged += 1
                current = db.query(FeedingMeasurement.status).filter(
                    FeedingMeasurement.record_id == record.id
                ).scalar()
                if current == "review":
                    job.to_review += 1
            job.last_id = record.id
            job.heartbeat_at = datetime.utcnow()
            db.commit()  # 每条提交：进程在任意时刻被杀掉，水位都不回退

        has_more = _base_query(db, job.scope, params).filter(
            FeedingRecord.id > job.last_id
        ).order_by(FeedingRecord.id).limit(1).first() is not None

        if not has_more:
            job.status = "done"
            job.finished_at = datetime.utcnow()
        else:
            job.status = "paused"
        job.heartbeat_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # 中断落账：已提交的水位保留，任务可续跑
        db.rollback()
        job = db.get(RecalcJob, job_id)
        job.status = "paused"
        job.error = f"{type(exc).__name__}: {exc}"
        db.commit()
        raise

    db.refresh(job)
    return _job_snapshot(job)


def run_to_completion(db: Session, job_id: int, batch_size: int = 100, max_batches: int = 10000) -> dict:
    """同步把任务跑完（测试/脚本场景）。"""
    for _ in range(max_batches):
        snapshot = run_batch(db, job_id, batch_size)
        if snapshot["status"] == "done":
            return snapshot
    raise RecalcError("超过最大批次数, 任务仍未完成")
