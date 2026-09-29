"""汇总回算与差额查询：差额逐笔可回原记录；周期分析只认真实换算口径。"""

import unittest
from datetime import date

from _db_case import MetrologyDBTest
from backend.app import models


class SummaryVarianceTest(MetrologyDBTest):
    def test_variance_lines_trace_back_to_source_records(self):
        _, batch = self.create_pond_batch()
        self.create_product_with_versions()

        # 旧口径：2 被当作 2 公斤；真实：2 袋 * 40kg = 80 公斤
        r1 = self.add_feeding(
            batch, date(2026, 2, 1), "通威103",
            raw_quantity=2, raw_unit="bag", package_label="40kg/袋",
            legacy_feed_quantity=2.0,
        )
        r2 = self.add_feeding(
            batch, date(2026, 5, 1), "103料",
            raw_quantity=2, raw_unit="bag", package_label="20kg/袋",
            legacy_feed_quantity=2.0,
        )
        # 待复核：旧口径混入 6，计量链不接收
        r3 = self.add_feeding(
            batch, date(2026, 5, 2), "杂牌X",
            raw_quantity=6, raw_unit="bag", legacy_feed_quantity=6.0,
        )

        resp = self.client.get(f"/api/analysis/feeding-variance/?batch_id={batch.id}")
        self.assertEqual(resp.status_code, 200)
        report = resp.json()
        self.assertEqual(report["legacy_total_kg"], 10.0)
        self.assertEqual(report["measured_total_kg"], 120.0)   # 80 + 40
        self.assertEqual(report["excluded_review_count"], 1)
        self.assertEqual(report["variance_kg"], 110.0)

        by_id = {line["record_id"]: line for line in report["lines"]}
        self.assertEqual(by_id[r1.id]["diff_kg"], 78.0)
        self.assertEqual(by_id[r1.id]["spec_version_no"], 1)
        self.assertEqual(by_id[r2.id]["diff_kg"], 38.0)
        self.assertEqual(by_id[r2.id]["spec_version_no"], 2)
        self.assertIsNone(by_id[r3.id]["measured_kg"])
        self.assertEqual(by_id[r3.id]["status"], "review")
        # 每行都带原始三要素，可直接回到原记录核对
        self.assertEqual(by_id[r1.id]["raw_quantity"], 2)
        self.assertEqual(by_id[r1.id]["raw_unit"], "bag")
        self.assertEqual(by_id[r1.id]["package_label"], "40kg/袋")

    def test_cycle_summary_recomputes_after_spec_correction_and_review_resolution(self):
        _, batch = self.create_pond_batch()
        product, (v1, v2) = self.create_product_with_versions()
        r1 = self.add_feeding(batch, date(2026, 2, 1), "通威103", raw_quantity=2, raw_unit="bag")
        r2 = self.add_feeding(batch, date(2026, 2, 2), "海大868", raw_quantity=2, raw_unit="bag")

        data = self.client.get(f"/api/analysis/cycle/{batch.id}/").json()
        self.assertEqual(data["feed_total"], 80.0)
        self.assertEqual(data["review_pending_count"], 1)

        # 供应商复核：2 月包装实际是 38kg/袋 -> 更正 + 区间回算
        self.client.patch(f"/api/feed-products/versions/{v1.id}/correct/", json={"kg_per_bag": 38.0})
        job = self.client.post("/api/recalc/jobs/", json={
            "scope": "spec_change", "product_id": product.id,
            "date_from": "2026-01-01", "date_to": "2026-04-01",
        }).json()
        self.client.post(f"/api/recalc/jobs/{job['id']}/run/?limit=10")

        # 待复核的另一产品补规格并裁定
        p2_resp = self.client.post("/api/feed-products/", json={
            "canonical_name": "海大牌868", "aliases": ["海大868"],
        })
        p2_id = p2_resp.json()["id"]
        self.client.post(f"/api/feed-products/{p2_id}/versions/", json={
            "kg_per_bag": 25.0, "effective_from": "2026-01-01", "package_label": "25kg/袋",
        })
        resolve = self.client.post(f"/api/feeding-reviews/{r2.id}/resolve/", json={"product_id": p2_id})
        self.assertEqual(resolve.status_code, 200)
        self.assertEqual(resolve.json()["quantity_kg"], 50.0)

        data = self.client.get(f"/api/analysis/cycle/{batch.id}/").json()
        self.assertEqual(data["feed_total"], 126.0)  # 76 + 50
        self.assertEqual(data["review_pending_count"], 0)
        self.assertIsNone(data["measurement_note"])

    def test_variance_filtered_by_product_and_date_window(self):
        _, batch = self.create_pond_batch()
        product, _ = self.create_product_with_versions()
        self.add_feeding(batch, date(2026, 2, 1), "通威103", raw_quantity=1, raw_unit="bag")
        self.add_feeding(batch, date(2026, 5, 1), "103料", raw_quantity=1, raw_unit="bag")

        # date_to 为不含上界：只统计 1~3 月
        resp = self.client.get(
            f"/api/analysis/feeding-variance/?product_id={product.id}"
            "&date_from=2026-01-01&date_to=2026-04-01"
        )
        report = resp.json()
        self.assertEqual(len(report["lines"]), 1)
        self.assertEqual(report["measured_total_kg"], 40.0)


if __name__ == "__main__":
    unittest.main()
