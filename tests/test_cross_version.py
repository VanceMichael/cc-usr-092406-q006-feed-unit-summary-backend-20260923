"""跨版本包装换算：投喂日期命中生效版本，袋/克/公斤统一口径，逐笔可追溯。"""

import unittest
from datetime import date

from _db_case import MetrologyDBTest
from backend.app.models import FeedingMeasurement
from backend.app.services.feeding import calculate_for_record


class CrossVersionPackagingTest(MetrologyDBTest):
    def test_bags_resolve_to_different_spec_versions_by_feeding_date(self):
        _, batch = self.create_pond_batch()
        product, (v1, v2) = self.create_product_with_versions()

        feb = self.add_feeding(batch, date(2026, 2, 15), "通威103", raw_quantity=2, raw_unit="袋",
                               package_label="40kg/袋")
        boundary = self.add_feeding(batch, date(2026, 3, 31), "通威103", raw_quantity=1, raw_unit="bag")
        apr = self.add_feeding(batch, date(2026, 4, 1), "103料", raw_quantity=2, raw_unit="bag",
                               package_label="20kg/袋")
        grams = self.add_feeding(batch, date(2026, 5, 1), "通威103成鱼料", raw_quantity=1500, raw_unit="g")

        self.assertEqual(feb.measurement.status, "ok")
        self.assertEqual(feb.measurement.quantity_kg, 80.0)
        self.assertEqual(feb.measurement.spec_version_id, v1.id)
        self.assertEqual(feb.measurement.spec_version_no, 1)
        self.assertEqual(feb.measurement.spec_kg_per_bag, 40.0)

        # 生效期左闭右开：3/31 仍是 v1，4/1 起是 v2
        self.assertEqual(boundary.measurement.spec_version_id, v1.id)
        self.assertEqual(apr.measurement.spec_version_id, v2.id)
        self.assertEqual(apr.measurement.quantity_kg, 40.0)

        self.assertEqual(grams.measurement.quantity_kg, 1.5)
        self.assertEqual(grams.measurement.spec_version_id, v2.id)

        # 原始数量/单位/包装完整保留
        self.assertEqual(feb.raw_quantity, 2)
        self.assertEqual(feb.raw_unit, "袋")
        self.assertEqual(feb.package_label, "40kg/袋")

    def test_mixed_full_name_short_name_and_lot_number_in_feed_type(self):
        _, batch = self.create_pond_batch()
        product, (v1, v2) = self.create_product_with_versions()

        r1 = self.add_feeding(batch, date(2026, 2, 1), "通威103 生产批号: TW20260108A",
                              raw_quantity=1, raw_unit="bag")
        r2 = self.add_feeding(batch, date(2026, 5, 1), "103料（20kg/袋）lot no. LOT-XYZ-22",
                              raw_quantity=1, raw_unit="bag")
        r3 = self.add_feeding(batch, date(2026, 5, 2), "通威103成鱼料 批号 TW20260420",
                              raw_quantity=1, raw_unit="bag")

        for r, expected_kg, ver in ((r1, 40.0, 1), (r2, 20.0, 2), (r3, 20.0, 2)):
            self.assertEqual(r.measurement.status, "ok", r.feed_type)
            self.assertEqual(r.measurement.product_id, product.id)
            self.assertEqual(r.measurement.quantity_kg, expected_kg)
            self.assertEqual(r.measurement.spec_version_no, ver)

    def test_cycle_analysis_uses_measured_kg_and_traceability_shows_version_per_record(self):
        _, batch = self.create_pond_batch()
        self.create_product_with_versions()
        self.add_feeding(batch, date(2026, 2, 1), "通威103", raw_quantity=2, raw_unit="bag")   # 80
        self.add_feeding(batch, date(2026, 5, 1), "103料", raw_quantity=2, raw_unit="bag")    # 40

        resp = self.client.get(f"/api/analysis/cycle/{batch.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["feed_total"], 120.0)
        self.assertEqual(data["review_pending_count"], 0)
        # 逐组说明采用的规格版本
        versions_in_breakdown = sorted(
            (item["spec_version_no"], item["total_kg"]) for item in data["feeding_breakdown"]
        )
        self.assertEqual(versions_in_breakdown, [(1, 80.0), (2, 40.0)])

        trace = self.client.get(f"/api/analysis/traceability/{batch.id}/").json()
        per_record = [
            (f["spec_version_no"], f["quantity_kg"], f["spec_kg_per_bag"], f["raw_unit"])
            for f in trace["feeding_records"]
        ]
        self.assertEqual(per_record, [(1, 80.0, 40.0, "bag"), (2, 40.0, 20.0, "bag")])

    def test_precision_3_decimal_in_summary(self):
        _, batch = self.create_pond_batch()
        self.create_product_with_versions(versions=[
            (20.04, date(2026, 1, 1), "20.04kg/袋"),
        ])
        self.add_feeding(batch, date(2026, 1, 5), "通威103", raw_quantity=3, raw_unit="袋")  # 60.12
        self.add_feeding(batch, date(2026, 1, 6), "通威103", raw_quantity=333, raw_unit="g")  # 0.333
        data = self.client.get(f"/api/analysis/cycle/{batch.id}/").json()
        self.assertEqual(data["feed_total"], 60.453)


if __name__ == "__main__":
    unittest.main()
