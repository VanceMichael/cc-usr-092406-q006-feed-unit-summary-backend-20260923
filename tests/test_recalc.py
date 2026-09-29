"""旧记录分批回算：中断续跑、重启接管、防重复换算、签署跳过。"""

import time
import unittest
from datetime import date, datetime, timedelta

from _db_case import MetrologyDBTest
from backend.app.database import SessionLocal
from backend.app import models
from backend.app.services import recalc


class RecalcResumeTest(MetrologyDBTest):
    def _seed_records(self, n=7):
        _, batch = self.create_pond_batch()
        self.create_product_with_versions()
        records = []
        for i in range(n):
            records.append(self.add_feeding(
                batch, date(2026, 2, 1), "通威103",
                raw_quantity=1, raw_unit="bag", measure=False,
            ))
        return records

    def test_batched_pause_and_resume_by_watermark(self):
        records = self._seed_records(7)
        job = recalc.create_job(self.db, "all")
        self.assertEqual(job["total"], 7)
        self.assertEqual(job["status"], "pending")

        s1 = recalc.run_batch(self.db, job["id"], limit=3)
        self.assertEqual(s1["status"], "paused")
        self.assertEqual(s1["processed"], 3)
        self.assertEqual(s1["last_id"], records[2].id)

        s2 = recalc.run_batch(self.db, job["id"], limit=3)
        self.assertEqual(s2["status"], "paused")
        self.assertEqual(s2["processed"], 6)
        self.assertEqual(s2["last_id"], records[5].id)

        s3 = recalc.run_batch(self.db, job["id"], limit=3)
        self.assertEqual(s3["status"], "done")
        self.assertEqual(s3["processed"], 7)
        self.assertIsNotNone(s3["finished_at"])

        # done 任务再跑直接返回，不重复换算
        s4 = recalc.run_batch(self.db, job["id"], limit=3)
        self.assertEqual(s4["status"], "done")
        self.assertEqual(s4["processed"], 7)

    def test_restart_takes_over_stale_running_job_and_never_double_converts(self):
        records = self._seed_records(5)
        job = recalc.create_job(self.db, "all")
        recalc.run_batch(self.db, job["id"], limit=2)
        calculated_at_first = {
            r.id: r.measurement.calculated_at for r in records[:2]
        }
        kg_first = {r.id: r.measurement.quantity_kg for r in records[:2]}

        # 模拟进程崩溃：任务停在 running，心跳停在很久以前
        self.db.query(models.RecalcJob).filter_by(id=job["id"]).update(
            {"status": "running", "heartbeat_at": datetime.utcnow() - timedelta(minutes=10)}
        )
        self.db.commit()

        # “重启”：换一个全新数据库会话接管续跑
        db2 = SessionLocal()
        try:
            snapshot = recalc.run_batch(db2, job["id"], limit=10)
            self.assertEqual(snapshot["status"], "done")
            self.assertEqual(snapshot["processed"], 5)
        finally:
            db2.close()

        # 已处理的前两条没有被重复换算：结果与换算时间戳保持不变
        self.db.expire_all()
        for rid, ts in calculated_at_first.items():
            m = self.db.query(models.FeedingMeasurement).filter_by(record_id=rid).first()
            self.assertEqual(m.quantity_kg, kg_first[rid])
            self.assertEqual(m.calculated_at, ts)

    def test_fresh_run_after_completion_counts_all_unchanged(self):
        records = self._seed_records(4)
        job1 = recalc.create_job(self.db, "all")
        recalc.run_to_completion(self.db, job1["id"], batch_size=2)

        job2 = recalc.create_job(self.db, "all")
        snapshot = recalc.run_to_completion(self.db, job2["id"], batch_size=2)
        self.assertEqual(snapshot["processed"], 0)
        self.assertEqual(snapshot["unchanged"], 4)

    def test_signed_off_records_skipped_during_recalc(self):
        records = self._seed_records(3)
        # 签署中间一条
        from backend.app.services.feeding import calculate_for_record
        calculate_for_record(self.db, records[1])
        records[1].signed_off = True
        records[1].signed_off_at = datetime.utcnow()
        self.db.commit()

        job = recalc.create_job(self.db, "all")
        snapshot = recalc.run_to_completion(self.db, job["id"], batch_size=2)
        self.assertEqual(snapshot["processed"], 2)
        self.assertEqual(snapshot["skipped_signed"], 1)

    def test_legacy_rows_only_having_feed_quantity_are_backfilled_as_kg_without_guessing(self):
        """旧汇总把所有数值当公斤；回算旧行时原样认定为公斤，袋数缺规格仍进复核。"""
        _, batch = self.create_pond_batch()
        self.create_product_with_versions()

        # 旧行：只有 feed_quantity（公斤），无原始三要素、无计量结果
        legacy_kg = models.FeedingRecord(
            batch_id=batch.id, feeding_date=date(2026, 2, 1),
            feed_type="通威103", feed_quantity=12.5,
        )
        # 旧行里按袋记的脏数据（数量被错放进公斤字段无法判定袋规格）不在本场景；
        # 真正按袋的旧行会以 raw_quantity/raw_unit 补录后换算。
        legacy_unknown = models.FeedingRecord(
            batch_id=batch.id, feeding_date=date(2026, 2, 2),
            feed_type="已退市某牌饲料", feed_quantity=3.0,
        )
        self.db.add_all([legacy_kg, legacy_unknown])
        self.db.commit()

        job = recalc.create_job(self.db, "all")
        snap = recalc.run_to_completion(self.db, job["id"], batch_size=10)
        self.assertEqual(snap["processed"], 2)
        self.assertEqual(snap["to_review"], 1)

        self.db.refresh(legacy_kg)
        self.assertEqual(legacy_kg.raw_quantity, 12.5)
        self.assertEqual(legacy_kg.raw_unit, "kg")
        self.assertEqual(legacy_kg.measurement.status, "ok")
        self.assertEqual(legacy_kg.measurement.quantity_kg, 12.5)

        self.db.refresh(legacy_unknown)
        self.assertEqual(legacy_unknown.measurement.status, "review")
        self.assertIsNone(legacy_unknown.measurement.quantity_kg)

    def test_concurrent_running_batch_is_rejected_while_fresh(self):
        self._seed_records(4)
        job = recalc.create_job(self.db, "all")
        recalc.run_batch(self.db, job["id"], limit=2)  # 结束为 paused
        # 手动制造新鲜 running 心跳
        self.db.query(models.RecalcJob).filter_by(id=job["id"]).update(
            {"status": "running", "heartbeat_at": datetime.utcnow()}
        )
        self.db.commit()
        with self.assertRaises(recalc.RecalcError):
            recalc.run_batch(self.db, job["id"], limit=2)


if __name__ == "__main__":
    unittest.main()
