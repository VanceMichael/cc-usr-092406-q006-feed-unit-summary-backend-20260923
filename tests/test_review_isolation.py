"""待复核隔离：规格缺失/同名冲突/单位非法不得计入任何汇总，且可人工裁定消解。"""

import unittest
from datetime import date

from _db_case import MetrologyDBTest
from backend.app import models
from backend.app.services import specs as spec_service
from backend.app.services import reviews as review_service
from backend.app.services.feeding import calculate_for_record


class ReviewIsolationTest(MetrologyDBTest):
    def _feed(self, batch, d, feed_type, **kw):
        return self.add_feeding(batch, d, feed_type, **kw)

    def test_missing_product_goes_to_review_and_is_excluded_from_summary(self):
        _, batch = self.create_pond_batch()
        product, _ = self.create_product_with_versions()
        self._feed(batch, date(2026, 2, 1), "通威103", raw_quantity=2, raw_unit="bag")  # 80kg
        unknown = self._feed(batch, date(2026, 2, 2), "杂牌999", raw_quantity=5, raw_unit="bag")

        self.assertEqual(unknown.measurement.status, "review")
        self.assertEqual(unknown.measurement.review_reason, "missing_product")
        self.assertIsNone(unknown.measurement.quantity_kg)

        open_items = self.db.query(models.ReviewItem).filter_by(status="open").all()
        self.assertEqual([i.record_id for i in open_items], [unknown.id])

        data = self.client.get(f"/api/analysis/cycle/{batch.id}/").json()
        self.assertEqual(data["feed_total"], 80.0)  # 待复核的 5 袋绝不按 5 公斤蒙混
        self.assertEqual(data["review_pending_count"], 1)
        self.assertIn("待复核", data["measurement_note"])

        # 复核列表 API
        r = self.client.get("/api/feeding-reviews/").json()
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["reason"], "missing_product")

    def test_missing_effective_spec_version_goes_to_review(self):
        _, batch = self.create_pond_batch()
        # 规格只从 4 月起生效
        product, _ = self.create_product_with_versions(versions=[(20.0, date(2026, 4, 1), "20kg/袋")])
        early = self._feed(batch, date(2026, 2, 1), "通威103", raw_quantity=2, raw_unit="bag")
        self.assertEqual(early.measurement.status, "review")
        self.assertEqual(early.measurement.review_reason, "missing_spec_version")
        data = self.client.get(f"/api/analysis/cycle/{batch.id}/").json()
        self.assertEqual(data["feed_total"], 0.0)
        self.assertEqual(data["review_pending_count"], 1)

        # 补登记 1 月起的版本后重算，自动消解且不猜比例
        spec_service.add_version(self.db, product_id=product.id,
                                 kg_per_bag=40.0, effective_from=date(2026, 1, 1),
                                 package_label="40kg/袋")
        self.db.commit()
        m, changed = calculate_for_record(self.db, early)
        self.assertTrue(changed)
        self.assertEqual(m.status, "ok")
        self.assertEqual(m.quantity_kg, 80.0)
        self.assertEqual(self.db.query(models.ReviewItem).filter_by(status="open").count(), 0)

    def test_same_name_conflict_goes_to_review_and_adjudication_resolves(self):
        _, batch = self.create_pond_batch()
        p1, _ = self.create_product_with_versions(name="通威103-A厂", aliases=["TW103"])
        # 第二家产品也声明了同一别名
        p2 = spec_service.create_product(self.db, "通威103-B厂", ["TW-103B"])
        spec_service.add_version(self.db, p2.id, 30.0, date(2026, 1, 1), "30kg/袋")
        self.db.commit()

        # 把 TW103 别名挂给第二个产品 = 冲突，接口必须拒绝而非静默覆盖
        resp = self.client.post(f"/api/feed-products/{p2.id}/aliases/", json={"alias": "TW103"})
        self.assertEqual(resp.status_code, 409)

        # 现场记录同时写了两个产品各自的名字：解析归并到多个产品 -> 冲突复核
        rec = models.FeedingRecord(batch_id=batch.id, feeding_date=date(2026, 2, 1),
                                   feed_type="TW103 TW-103B", raw_quantity=1, raw_unit="bag")
        self.db.add(rec)
        self.db.flush()
        m, _ = calculate_for_record(self.db, rec)
        self.db.commit()
        self.assertEqual(m.status, "review")
        self.assertEqual(m.review_reason, "product_name_conflict")
        data = self.client.get(f"/api/analysis/cycle/{batch.id}/").json()
        self.assertEqual(data["feed_total"], 0.0)

        # 同名裁定：B 厂实际就是 A 厂换包装，合并到 A
        result = spec_service.adjudicate_same_name(self.db, p2.id, p1.id)
        self.db.commit()
        self.db.refresh(rec)
        self.assertEqual(rec.measurement.status, "ok")
        self.assertEqual(rec.measurement.product_id, p1.id)
        self.assertEqual(rec.measurement.quantity_kg, 40.0)  # 采用 A 厂 2 月生效的 40kg/袋
        self.assertIn(rec.id, result["recalculated_records"])

    def test_signed_off_records_are_locked_even_when_spec_changes(self):
        _, batch = self.create_pond_batch()
        product, (v1, v2) = self.create_product_with_versions()
        rec = self._feed(batch, date(2026, 2, 1), "通威103", raw_quantity=2, raw_unit="bag")
        self.assertEqual(rec.measurement.quantity_kg, 80.0)

        # 签署
        resp = self.client.post(f"/api/feeding-records/{rec.id}/sign-off/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["signed_off"])

        # 更正 v1 净重为 25（供应商更正旧包装）
        r = self.client.patch(f"/api/feed-products/versions/{v1.id}/correct/",
                              json={"kg_per_bag": 25.0, "package_label": "25kg/袋"})
        self.assertEqual(r.status_code, 200)

        # 试图 PUT 修改签署记录的计量字段 -> 409
        blocked = self.client.put(f"/api/feeding-records/{rec.id}/", json={"raw_quantity": 3})
        self.assertEqual(blocked.status_code, 409)

        # 区间回算：签署记录跳过，保留签署时的 80kg
        job = self.client.post("/api/recalc/jobs/", json={
            "scope": "spec_change", "product_id": product.id,
            "date_from": "2026-01-01", "date_to": "2026-04-01",
        }).json()
        self.assertEqual(job["total"], 1)
        result = self.client.post(f"/api/recalc/jobs/{job['id']}/run/?limit=10").json()
        self.assertEqual(result["skipped_signed"], 1)
        self.assertEqual(result["processed"], 0)
        self.db.refresh(rec)
        self.assertEqual(rec.measurement.quantity_kg, 80.0)
        self.assertEqual(rec.measurement.spec_kg_per_bag, 40.0)  # 快照保留签署时规格

    def test_unsigned_record_in_affected_interval_is_recalculated(self):
        _, batch = self.create_pond_batch()
        product, (v1, v2) = self.create_product_with_versions()
        rec = self._feed(batch, date(2026, 2, 1), "通威103", raw_quantity=2, raw_unit="bag")

        r = self.client.patch(f"/api/feed-products/versions/{v1.id}/correct/",
                              json={"kg_per_bag": 38.0})
        self.assertEqual(r.status_code, 200)
        job = self.client.post("/api/recalc/jobs/", json={
            "scope": "spec_change", "product_id": product.id,
            "date_from": "2026-01-01", "date_to": "2026-04-01",
        }).json()
        self.client.post(f"/api/recalc/jobs/{job['id']}/run/?limit=10")
        self.db.refresh(rec)
        self.assertEqual(rec.measurement.quantity_kg, 76.0)
        self.assertEqual(rec.measurement.spec_kg_per_bag, 38.0)

        # 区间外（v2 区间）不应被该任务纳入
        may = self._feed(batch, date(2026, 5, 1), "103料", raw_quantity=2, raw_unit="bag")
        self.assertEqual(may.measurement.quantity_kg, 40.0)
        job2 = self.client.post("/api/recalc/jobs/", json={
            "scope": "spec_change", "product_id": product.id,
            "date_from": "2026-01-01", "date_to": "2026-04-01",
        }).json()
        self.assertEqual(job2["total"], 1)  # 只有 2 月那条

    def test_review_resolve_accepts_product_but_never_a_guessed_ratio(self):
        _, batch = self.create_pond_batch()
        unknown = self._feed(batch, date(2026, 2, 1), "海大868", raw_quantity=2, raw_unit="bag")

        # 还没有产品时直接裁定 -> 409
        r = self.client.post(f"/api/feeding-reviews/{unknown.id}/resolve/", json={"product_id": 999})
        self.assertEqual(r.status_code, 409)

        # 建产品但缺生效规格：裁定产品后复核仍开放（不能为对平数字编一个每袋重量）
        p = spec_service.create_product(self.db, "海大牌868", ["海大868"])
        self.db.commit()
        result = review_service.resolve_record(self.db, unknown.id, p.id)
        self.db.commit()
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["review_reason"], "missing_spec_version")

        spec_service.add_version(self.db, p.id, 25.0, date(2026, 1, 1), "25kg/袋")
        self.db.commit()
        result = review_service.resolve_record(self.db, unknown.id, p.id)
        self.db.commit()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["quantity_kg"], 50.0)
        self.db.refresh(unknown)
        self.assertEqual(unknown.measurement.status, "ok")
        self.assertEqual(self.db.query(models.ReviewItem).filter_by(status="open").count(), 0)


if __name__ == "__main__":
    unittest.main()
