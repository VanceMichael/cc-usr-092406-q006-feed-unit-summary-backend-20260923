"""规格裁定与新增投喂并发：按版本（同生效日唯一 + 投喂日命中）决定采用哪一版。"""

import threading
import unittest
from datetime import date

from sqlalchemy.exc import IntegrityError, OperationalError

from _db_case import MetrologyDBTest
from backend.app.database import SessionLocal
from backend.app import models
from backend.app.services import specs as spec_service
from backend.app.services import recalc
from backend.app.services.feeding import calculate_for_record


class ConcurrentVersionAdjudicationTest(MetrologyDBTest):
    def test_same_effective_date_double_registration_has_single_winner(self):
        """两个线程并发登记同一生效日的版本：数据库唯一约束保证只有一版胜出。"""
        product, _ = self.create_product_with_versions()
        outcomes = []

        barrier = threading.Barrier(2)

        def register(kg):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                try:
                    spec_service.add_version(
                        db, product.id, kg, date(2026, 6, 1), f"{kg}kg/袋"
                    )
                    db.commit()
                    outcomes.append(("ok", kg))
                except (spec_service.SpecError, IntegrityError, OperationalError):
                    db.rollback()
                    outcomes.append(("lost", kg))
            finally:
                db.close()

        t1 = threading.Thread(target=register, args=(20.0,))
        t2 = threading.Thread(target=register, args=(22.0,))
        t1.start(); t2.start(); t1.join(5); t2.join(5)

        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sorted(s for s, _ in outcomes), ["lost", "ok"])

        versions = spec_service.list_versions(self.db, product.id)
        june = [v for v in versions if v.effective_from == date(2026, 6, 1)]
        self.assertEqual(len(june), 1)

    def test_new_feeding_and_new_version_converge_to_committed_version(self):
        """
        并发交错：一边新增投喂（日期在新版生效日），一边登记新版。
        无论提交先后，最终经一次回算后所有记录都确定地采用已提交版本，不存在两可。
        """
        _, batch = self.create_pond_batch()
        product, _ = self.create_product_with_versions()  # v1 到 3/31, v2 从 4/1（20kg）

        errors = []

        def feed_worker():
            db = SessionLocal()
            try:
                rec = models.FeedingRecord(
                    batch_id=batch.id, feeding_date=date(2026, 7, 1),
                    feed_type="通威103", raw_quantity=2, raw_unit="bag",
                )
                db.add(rec)
                db.commit()
                calculate_for_record(db, rec)  # v3 尚未提交时 -> 缺规格复核
                db.commit()
            except Exception as exc:  # pragma: no cover - 记录失败用例
                errors.append(exc)
            finally:
                db.close()

        def spec_worker():
            db = SessionLocal()
            try:
                spec_service.add_version(db, product.id, 18.0, date(2026, 7, 1), "18kg/袋")
                db.commit()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                db.close()

        t1 = threading.Thread(target=feed_worker)
        t2 = threading.Thread(target=spec_worker)
        t1.start(); t2.start(); t1.join(10); t2.join(10)
        self.assertEqual(errors, [])

        # 收敛：回算后 7/1 记录确定采用 v3（18kg），2 袋 = 36kg
        job = recalc.create_job(self.db, "all")
        recalc.run_to_completion(self.db, job["id"], batch_size=10)
        rec = self.db.query(models.FeedingRecord).filter_by(feeding_date=date(2026, 7, 1)).one()
        self.assertEqual(rec.measurement.status, "ok")
        self.assertEqual(rec.measurement.spec_version_no, 3)
        self.assertEqual(rec.measurement.spec_kg_per_bag, 18.0)
        self.assertEqual(rec.measurement.quantity_kg, 36.0)

        # 此后再来的同日期投喂直接命中已提交的 v3
        later = self.add_feeding(batch, date(2026, 7, 2), "103料", raw_quantity=1, raw_unit="袋")
        self.assertEqual(later.measurement.spec_version_no, 3)
        self.assertEqual(later.measurement.quantity_kg, 18.0)

    def test_version_interval_overlap_rejected(self):
        product, _ = self.create_product_with_versions()
        with self.assertRaises(spec_service.SpecError):
            # 2/1 落在 v1 [1/1, 4/1) 区间内
            spec_service.add_version(self.db, product.id, 39.0, date(2026, 2, 1))


class FeedingApiChainTest(MetrologyDBTest):
    def test_create_with_raw_units_and_legacy_fallback(self):
        _, batch = self.create_pond_batch()
        self.create_product_with_versions()

        r = self.client.post("/api/feeding-records/", json={
            "batch_id": batch.id, "feeding_date": "2026-02-01",
            "feed_type": "通威103", "raw_quantity": 2, "raw_unit": "袋",
            "package_label": "40kg/袋",
        })
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["measurement"]["status"], "ok")
        self.assertEqual(body["measurement"]["quantity_kg"], 80.0)
        self.assertEqual(body["measurement"]["spec_version_no"], 1)

        # 缺单位被拒
        r = self.client.post("/api/feeding-records/", json={
            "batch_id": batch.id, "feeding_date": "2026-02-02",
            "feed_type": "通威103", "raw_quantity": 2,
        })
        self.assertEqual(r.status_code, 422)

        # 无法识别的单位被拒（不得猜测）
        r = self.client.post("/api/feeding-records/", json={
            "batch_id": batch.id, "feeding_date": "2026-02-03",
            "feed_type": "通威103", "raw_quantity": 2, "raw_unit": "桶",
        })
        self.assertEqual(r.status_code, 422)

        # 旧口径只给 feed_quantity：按公斤受理
        r = self.client.post("/api/feeding-records/", json={
            "batch_id": batch.id, "feeding_date": "2026-02-04",
            "feed_type": "通威103", "feed_quantity": 12.0,
        })
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["measurement"]["quantity_kg"], 12.0)

    def test_sign_off_blocked_while_review(self):
        _, batch = self.create_pond_batch()
        rec = self.add_feeding(batch, date(2026, 2, 1), "神秘饲料",
                               raw_quantity=1, raw_unit="bag")
        r = self.client.post(f"/api/feeding-records/{rec.id}/sign-off/")
        self.assertEqual(r.status_code, 409)
        self.db.refresh(rec)
        self.assertFalse(rec.signed_off)


if __name__ == "__main__":
    unittest.main()
