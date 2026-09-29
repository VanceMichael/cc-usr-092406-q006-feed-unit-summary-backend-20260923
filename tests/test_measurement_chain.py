"""计量链端到端测试。

覆盖：
- 跨版本包装（换供应商新版本、历史补录、临时包装区间与自动延续、生效边界）；
- 规格缺失/同名冲突/不支持单位进入待复核且隔离，不猜测比例；
- 签署后包装更正只影响未签署且真正受影响的记录；
- 旧记录分批回填：中断、重启续跑、防止重复换算；
- 规格裁定与新增投喂按生效版本决定采用哪一版（含创建先后与重叠取新版）；
- 差额查询可回到原记录；周期分析与追溯逐笔给出规格版本；汇总回算；
- 旧 SQLite 库迁移登记。
"""
import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Pond, Batch, FeedingRecord, FeedProduct, FeedProductVersion,
    CONVERSION_PENDING, CONVERSION_CONVERTED, CONVERSION_REVIEW,
)
from app.services import conversion  # noqa: E402
from app.services.units import convert_to_kg  # noqa: E402
from app.routers.feed_products import _add_version  # noqa: E402

client = TestClient(app)


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def make_pond_batch(db, batch_number="B001", stocking=date(2026, 1, 1)):
    pond = Pond(name=f"塘_{batch_number}", area=10, water_depth=2, species="罗非鱼")
    db.add(pond)
    db.flush()
    batch = Batch(
        batch_number=batch_number, pond_id=pond.id, species="罗非鱼",
        stocking_date=stocking,
    )
    db.add(batch)
    db.flush()
    return pond, batch


def create_product(full_name, short_name=None, aliases=None,
                   package_kg=40.0, effective_from=date(2026, 1, 1)):
    resp = client.post("/api/feed-products/", json={
        "full_name": full_name,
        "short_name": short_name,
        "aliases": aliases,
        "package_kg": package_kg,
        "effective_from": str(effective_from),
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def feed(batch_id, day, feed_type, quantity, unit, package_kg=None,
         package_batch_no=None):
    payload = {
        "batch_id": batch_id,
        "feeding_date": str(day),
        "feed_type": feed_type,
        "quantity": quantity,
        "unit": unit,
    }
    if package_kg is not None:
        payload["package_kg"] = package_kg
    if package_batch_no is not None:
        payload["package_batch_no"] = package_batch_no
    resp = client.post("/api/feeding-records/", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


class PrecisionTest(unittest.TestCase):
    def test_units_and_rounding_half_up(self):
        self.assertEqual(convert_to_kg(2, "袋", 40), Decimal("80.000"))
        self.assertEqual(convert_to_kg(1234, "g"), Decimal("1.234"))
        # 四舍五入 HALF_UP：1234.56g = 1.23456kg -> 1.235kg
        self.assertEqual(convert_to_kg(1234.56, "克"), Decimal("1.235"))
        self.assertEqual(convert_to_kg(2.5, "bags", 20), Decimal("50.000"))
        # 袋计量缺包装不允许猜测
        from app.services.units import ConversionError
        with self.assertRaises(ConversionError):
            convert_to_kg(1, "bag")


class CrossVersionTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        db = SessionLocal()
        self.pond, self.batch = make_pond_batch(db)
        db.commit()
        self.batch_id = self.batch.id
        db.close()
        self.product = create_product(
            "罗非鱼配合饲料", short_name="罗非料",
            package_kg=40.0, effective_from=date(2026, 1, 1),
        )
        self.pid = self.product["id"]

    def test_supplier_package_change_open_ended(self):
        # 换供应商：新包装 25kg/袋 自 2026-06-01 生效（旧版本自动截止）
        r_before = feed(self.batch_id, date(2026, 5, 31), "罗非料", 2, "bag")
        resp = client.post("/api/feed-products/correct-package/", json={
            "product_id": self.pid,
            "package_kg": 25.0,
            "effective_from": "2026-06-01",
            "change_reason": "供应商更换包装",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        r_after = feed(self.batch_id, date(2026, 6, 1), "罗非料", 2, "袋")

        self.assertEqual(r_before["feed_quantity"], 80.0)
        self.assertEqual(r_after["feed_quantity"], 50.0)
        self.assertNotEqual(r_before["spec_version_id"], r_after["spec_version_id"])
        self.assertEqual(r_after["resolved_package_kg"], 25.0)
        # 旧记录未签署，随新版本回算：5-31 仍是 80（不受影响），无多余重算
        again = client.get(f"/api/feeding-records/{r_before['id']}/").json()
        self.assertEqual(again["feed_quantity"], 80.0)

    def test_backdated_historical_version(self):
        # 事后补录历史规格 35kg [2025-01-01, 2026-01-01)
        resp = client.post(f"/api/feed-products/{self.pid}/versions/", json={
            "package_kg": 35.0,
            "effective_from": "2025-01-01",
            "effective_to": "2026-01-01",
            "change_reason": "补录历史包装",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        old = feed(self.batch_id, date(2025, 12, 31), "罗非鱼配合饲料", 2, "bag")
        new = feed(self.batch_id, date(2026, 1, 1), "罗非鱼配合饲料", 2, "bag")
        self.assertEqual(old["feed_quantity"], 70.0)
        self.assertEqual(new["feed_quantity"], 80.0)

    def test_temporary_correction_window_with_continuation(self):
        # 临时包装：25kg 仅 6 月有效，系统自动追加 7 月起恢复 40kg 的延续版本
        resp = client.post("/api/feed-products/correct-package/", json={
            "product_id": self.pid,
            "package_kg": 25.0,
            "effective_from": "2026-06-01",
            "effective_to": "2026-07-01",
        })
        self.assertEqual(resp.status_code == 200, True)
        body = resp.json()
        self.assertEqual(body["effective_to"], "2026-07-01")  # 返回的是更正区间版本
        may = feed(self.batch_id, date(2026, 5, 31), "罗非料", 1, "bag")
        jun = feed(self.batch_id, date(2026, 6, 15), "罗非料", 1, "bag")
        jul = feed(self.batch_id, date(2026, 7, 2), "罗非料", 1, "bag")
        self.assertEqual(may["feed_quantity"], 40.0)
        self.assertEqual(jun["feed_quantity"], 25.0)
        self.assertEqual(jul["feed_quantity"], 40.0)
        # 5 月与 7 月分别落在 v1 与自动延续版本（版本不同），但采用的袋规格同为 40kg
        self.assertNotEqual(may["spec_version_id"], jun["spec_version_id"])
        self.assertNotEqual(jun["spec_version_id"], jul["spec_version_id"])
        self.assertEqual(may["resolved_package_kg"], jul["resolved_package_kg"])

    def test_overlapping_version_rejected(self):
        resp = client.post(f"/api/feed-products/{self.pid}/versions/", json={
            "package_kg": 30.0,
            "effective_from": "2026-02-01",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        # 与 v1 [2026-01-01, 2026-02-01) 之后再插交叉区间应被拒绝
        resp = client.post(f"/api/feed-products/{self.pid}/versions/", json={
            "package_kg": 33.0,
            "effective_from": "2026-01-15",
            "effective_to": "2026-02-15",
        })
        self.assertEqual(resp.status_code, 400)

    def test_lot_extracted_from_mixed_text(self):
        r = feed(self.batch_id, date(2026, 3, 1),
                 "罗非鱼配合饲料(生产批号:LOT20260301)", 1, "bag")
        self.assertEqual(r["feed_quantity"], 40.0)
        self.assertIn("LOT20260301", (r["package_batch_no"] or ""))

    def test_record_time_package_snapshot_wins(self):
        # 现场登记的"当时包装"优先于规格版本（现场快照）
        r = feed(self.batch_id, date(2026, 3, 1), "罗非料", 2, "bag",
                 package_kg=38.5)
        self.assertEqual(r["feed_quantity"], 77.0)


class ReviewIsolationTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        db = SessionLocal()
        self.pond, self.batch = make_pond_batch(db)
        db.commit()
        self.batch_id = self.batch.id
        db.close()
        self.product = create_product(
            "罗非鱼配合饲料", short_name="罗非料",
            package_kg=40.0, effective_from=date(2026, 1, 1),
        )
        self.good = feed(self.batch_id, date(2026, 3, 1), "罗非料", 1, "bag")

    def test_missing_spec_goes_to_review_and_is_isolated(self):
        bad = feed(self.batch_id, date(2026, 3, 2), "未知牌饲料X", 3, "bag")
        self.assertEqual(bad["conversion_status"], CONVERSION_REVIEW)
        self.assertEqual(bad["review_reason"], "missing_spec")
        self.assertIsNone(bad["feed_quantity"])

        # 待复核清单可见
        review = client.get("/api/reconciliation/review/").json()
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["id"], bad["id"])

        # 周期分析：只有 converted 公斤计入，复核记录单独计数
        cycle = client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertEqual(cycle["feed_total"], 40.0)
        self.assertEqual(cycle["feeding_summary"]["review_count"], 1)
        self.assertEqual(cycle["feeding_summary"]["converted_count"], 1)

    def test_ambiguous_same_name_goes_to_review_then_merge_adjudicates(self):
        other = create_product(
            "海水鱼配合饲料", short_name=None, aliases="罗非料",
            package_kg=20.0, effective_from=date(2026, 1, 1),
        )
        # 重新换算已存在的 good：此时"罗非料"同时指向两个产品 -> 冲突
        resp = client.put(f"/api/feeding-records/{self.good['id']}/",
                          json={"notes": "触发重算"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["conversion_status"], CONVERSION_REVIEW)
        self.assertEqual(resp.json()["review_reason"], "ambiguous_product")
        self.assertIsNone(resp.json()["feed_quantity"])

        # 人工裁定同名：把海水鱼产品并入罗非鱼产品
        resp = client.post("/api/feed-products/merge/", json={
            "source_id": other["id"],
            "target_id": self.product["id"],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        r = client.get(f"/api/feeding-records/{self.good['id']}/").json()
        self.assertEqual(r["conversion_status"], CONVERSION_CONVERTED)
        self.assertEqual(r["feed_quantity"], 40.0)
        self.assertEqual(r["product_id"], self.product["id"])

    def test_unsupported_unit_and_explicit_resolve(self):
        bad = feed(self.batch_id, date(2026, 3, 3), "罗非料", 5, "箱")
        self.assertEqual(bad["conversion_status"], CONVERSION_REVIEW)
        self.assertEqual(bad["review_reason"], "unsupported_unit")

        resp = client.post(f"/api/feeding-records/{bad['id']}/resolve-review/",
                           json={"unit": "g", "quantity": 1500})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["conversion_status"], CONVERSION_CONVERTED)
        self.assertEqual(body["feed_quantity"], 1.5)

    def test_missing_spec_date_gap(self):
        # 产品存在但投喂日没有任何生效版本
        create_product(
            "对虾饲料", package_kg=20.0, effective_from=date(2027, 1, 1),
        )
        r = feed(self.batch_id, date(2026, 3, 5), "对虾饲料", 1, "bag")
        self.assertEqual(r["conversion_status"], CONVERSION_REVIEW)
        self.assertEqual(r["review_reason"], "missing_spec")

    def test_signing_freezes_signed_records_on_correction(self):
        signed = feed(self.batch_id, date(2026, 6, 10), "罗非料", 2, "bag")  # 80
        unsigned = feed(self.batch_id, date(2026, 6, 11), "罗非料", 2, "bag")  # 80
        sign = client.post("/api/feeding-records/sign/", json={
            "record_ids": [signed["id"]]
        })
        self.assertEqual(sign.json()["signed"], 1)

        # 已签署记录不能改计量字段
        denied = client.put(f"/api/feeding-records/{signed['id']}/",
                            json={"quantity": 3})
        self.assertEqual(denied.status_code, 409)

        # 包装更正：6 月起 25kg
        client.post("/api/feed-products/correct-package/", json={
            "product_id": self.product["id"],
            "package_kg": 25.0,
            "effective_from": "2026-06-01",
        })
        frozen = client.get(f"/api/feeding-records/{signed['id']}/").json()
        moved = client.get(f"/api/feeding-records/{unsigned['id']}/").json()
        self.assertEqual(frozen["feed_quantity"], 80.0)       # 签署冻结
        self.assertEqual(frozen["spec_version_id"], signed["spec_version_id"])
        self.assertEqual(moved["feed_quantity"], 50.0)        # 未签署回算


class BackfillTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        db = SessionLocal()
        self.db = db
        self.pond, self.batch = make_pond_batch(db)
        self.batch_id = self.batch.id
        db.commit()
        self.product = create_product(
            "罗非鱼配合饲料", package_kg=40.0,
            effective_from=date(2026, 1, 1),
        )
        # 产品规格已就绪后，直接构造 5 条历史 pending 记录（模拟迁移登记、
        # 尚未回填的旧库状态），等待分批回填拾取
        for i in range(5):
            db.add(FeedingRecord(
                batch_id=self.batch_id, feeding_date=date(2026, 2, 1 + i),
                feed_type="罗非鱼配合饲料",
                feed_quantity=None, quantity=2, unit="bag",
                conversion_status=CONVERSION_PENDING,
            ))
        db.commit()

    def tearDown(self):
        self.db.close()

    def test_interrupt_resume_and_no_double_conversion(self):
        # 第一批：只处理 2 条并中断
        r1 = client.post("/api/reconciliation/backfill/",
                         json={"limit": 2, "interrupt": True})
        self.assertEqual(r1.status_code, 200, r1.text)
        b1 = r1.json()
        self.assertEqual(b1["processed_in_run"], 2)
        self.assertEqual(b1["pending_remaining"], 3)
        self.assertEqual(b1["batch"]["status"], "interrupted")
        bid = b1["batch"]["id"]

        # 重启续跑（新 HTTP 调用即新会话）：再处理 2 条
        r2 = client.post(f"/api/reconciliation/backfill/?batch_id={bid}",
                         json={"limit": 2, "interrupt": True})
        b2 = r2.json()
        self.assertEqual(b2["batch"]["id"], bid)
        self.assertEqual(b2["processed_in_run"], 2)
        self.assertEqual(b2["pending_remaining"], 1)

        # 再续跑直到完成
        r3 = client.post(f"/api/reconciliation/backfill/?batch_id={bid}",
                         json={"limit": 10})
        b3 = r3.json()
        self.assertEqual(b3["processed_in_run"], 1)
        self.assertEqual(b3["pending_remaining"], 0)
        self.assertEqual(b3["batch"]["status"], "completed")

        # 对已完成批次再跑一次：安全空操作，不产生重复换算
        r4 = client.post(f"/api/reconciliation/backfill/?batch_id={bid}",
                         json={"limit": 10})
        self.assertEqual(r4.json()["processed_in_run"], 0)

        # 结果核对：5 条 * 2 袋 * 40kg = 400kg；批次计数等于唯一记录数
        records = client.get("/api/feeding-records/",
                             params={"batch_id": self.batch_id}).json()
        self.assertEqual(len(records), 5)
        self.assertTrue(all(r["conversion_status"] == CONVERSION_CONVERTED
                            for r in records))
        self.assertAlmostEqual(sum(r["feed_quantity"] for r in records), 400.0)
        batch = client.get(f"/api/reconciliation/batches/{bid}/").json()
        self.assertEqual(batch["converted_count"], 5)
        self.assertEqual(batch["total_seen"], 5)


class VersionDecisionTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        db = SessionLocal()
        self.db = db
        self.pond, self.batch = make_pond_batch(db)
        db.commit()
        self.batch_id = self.batch.id
        self.product = create_product(
            "罗非鱼配合饲料", package_kg=40.0,
            effective_from=date(2026, 1, 1),
        )
        self.pid = self.product["id"]

    def tearDown(self):
        self.db.close()

    def test_version_chosen_by_effective_date_regardless_of_order(self):
        # 投喂记录先于新版本存在（未换算提交），随后新版本在另一会话提交，
        # 记录换算时采用当时已生效的最新版本。
        s1 = SessionLocal()
        rec = FeedingRecord(
            batch_id=self.batch_id, feeding_date=date(2026, 6, 1),
            feed_type="罗非鱼配合饲料", quantity=2, unit="bag",
            conversion_status=CONVERSION_PENDING,
        )
        s1.add(rec)
        s1.commit()
        rec_id = rec.id
        s1.close()

        s2 = SessionLocal()
        _add_version(s2, self.pid, 25.0, date(2026, 6, 1), None, "换包装")
        conversion.recompute_affected(s2, product_ids=[self.pid])
        s2.commit()
        s2.close()

        self.db.expire_all()
        refreshed = self.db.query(FeedingRecord).filter_by(id=rec_id).first()
        self.assertEqual(refreshed.feed_quantity, 50.0)
        self.assertEqual(refreshed.spec_version.version_number, 2)

        # 另一条新建投喂：6-1 前仍按 v1，6-1 起按 v2 —— 版本而非创建时间决定
        before = feed(self.batch_id, date(2026, 5, 31), "罗非鱼配合饲料", 1, "bag")
        after = feed(self.batch_id, date(2026, 6, 1), "罗非鱼配合饲料", 1, "bag")
        self.assertEqual(before["spec_version_id"], self.product["versions"][0]["id"])
        self.assertEqual(after["feed_quantity"], 25.0)

    def test_overlapping_versions_take_higher_version_number(self):
        # 极端并发下出现同日重叠版本时，确定性地采用版本号更大的一版
        db = SessionLocal()
        product = db.query(FeedProduct).filter_by(id=self.pid).first()
        v1 = product.versions[0]
        db.add(FeedProductVersion(
            product_id=self.pid, version_number=2, package_kg=25.0,
            effective_from=v1.effective_from, effective_to=None,
        ))
        db.commit()
        r = feed(self.batch_id, date(2026, 3, 1), "罗非鱼配合饲料", 2, "bag")
        self.assertEqual(r["resolved_package_kg"], 25.0)
        self.assertEqual(r["feed_quantity"], 50.0)
        db.close()


class VarianceAndAnalysisTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        db = SessionLocal()
        self.db = db
        self.pond, self.batch = make_pond_batch(db)
        db.commit()
        self.batch_id = self.batch.id
        db.close()
        self.product = create_product(
            "罗非鱼配合饲料", short_name="罗非料",
            package_kg=40.0, effective_from=date(2026, 1, 1),
        )
        self.r1 = feed(self.batch_id, date(2026, 3, 1), "罗非料", 2, "bag")  # 80
        self.r2 = feed(self.batch_id, date(2026, 3, 2), "罗非料", 500, "g")  # 0.5

    def test_variance_drills_back_to_source_records(self):
        # 一条待复核记录必须在差额报告中隔离可见
        bad = feed(self.batch_id, date(2026, 3, 3), "杂牌料", 1, "bag")

        resp = client.get("/api/reconciliation/variance/", params={
            "warehouse_bags": 2.0,
            "warehouse_package_kg": 40.0,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        report = resp.json()
        self.assertEqual(report["warehouse_kg"], 80.0)
        self.assertAlmostEqual(report["converted_kg"], 80.5)
        # 80.0 - 80.5 = -0.5
        self.assertAlmostEqual(report["variance_kg"], -0.5)
        self.assertEqual(len(report["converted_records"]), 2)
        self.assertEqual(len(report["review_records"]), 1)
        ids = {r["record_id"] for r in report["converted_records"]}
        self.assertIn(self.r1["id"], ids)
        # 明细逐笔带原始数量/单位/包装与规格版本
        line = next(r for r in report["converted_records"]
                    if r["record_id"] == self.r1["id"])
        self.assertEqual(line["original_quantity"], 2)
        self.assertEqual(line["original_unit"], "bag")
        self.assertEqual(line["spec_version_number"], 1)
        self.assertEqual(line["spec_package_kg"], 40.0)
        self.assertEqual(report["review_records"][0]["record_id"], bad["id"])

    def test_cycle_and_traceability_explain_version_per_record(self):
        cycle = client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertAlmostEqual(cycle["feed_total"], 80.5)
        groups = cycle["feeding_summary"]["by_product_version"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["spec_version_number"], 1)
        self.assertEqual(groups[0]["converted_kg"], 80.5)

        trace = client.get(
            f"/api/analysis/traceability/{self.batch_id}/"
        ).json()
        lines = trace["feeding_records"]
        self.assertEqual(len(lines), 2)
        first = lines[0]
        self.assertEqual(first["record_id"], self.r1["id"])
        self.assertEqual(first["original_unit"], "bag")
        self.assertEqual(first["product_name"], "罗非鱼配合饲料")
        self.assertEqual(first["spec_version_number"], 1)
        self.assertEqual(first["quantity_kg"], 80.0)
        self.assertIn(first["conversion_status"], ("converted",))

    def test_summary_recomputes_after_package_correction(self):
        client.post("/api/feed-products/correct-package/", json={
            "product_id": self.product["id"],
            "package_kg": 25.0,
            "effective_from": "2026-03-02",
        })
        cycle = client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        # r1=80(3-1 不受影响), r2 是克计量不受袋规格影响 -> 80.5 不变；
        # 新增一条 3-2 的袋记录验证回算
        feed(self.batch_id, date(2026, 3, 2), "罗非料", 2, "bag")
        cycle = client.get(f"/api/analysis/cycle/{self.batch_id}/").json()
        self.assertAlmostEqual(cycle["feed_total"], 80.5 + 50.0)


class LegacyMigrationTest(unittest.TestCase):
    def test_old_sqlite_db_is_registered_as_pending_kg(self):
        from sqlalchemy import create_engine, inspect, text
        legacy = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        legacy.close()
        eng = create_engine(f"sqlite:///{legacy.name}")
        with eng.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE feeding_records ("
                "id INTEGER PRIMARY KEY, batch_id INTEGER, feeding_date DATE, "
                "feed_type VARCHAR(100), feed_quantity FLOAT, created_at DATETIME)"
            )
            conn.exec_driver_sql(
                "INSERT INTO feeding_records (batch_id, feeding_date, feed_type, feed_quantity) "
                "VALUES (1, '2026-03-01', '老饲料', 40), (1, '2026-03-02', '老饲料', 20)"
            )

        from app.migrations import run_migrations
        run_migrations(eng)

        info = {c["name"]: c for c in inspect(eng).get_columns("feeding_records")}
        for needed in ("quantity", "unit", "conversion_status", "spec_version_id",
                       "resolved_package_kg", "signed_at"):
            self.assertIn(needed, info)
        # 旧的 feed_quantity NOT NULL 约束必须已解除：待复核记录要能置空公斤数
        self.assertEqual(info["feed_quantity"]["nullable"], True)

        with eng.begin() as conn:
            rows = conn.execute(text(
                "SELECT feed_quantity, quantity, unit, conversion_status "
                "FROM feeding_records ORDER BY id"
            )).all()
            # 迁移后表上置空公斤数不报错
            conn.execute(text("UPDATE feeding_records SET feed_quantity = NULL WHERE id = 1"))
        self.assertEqual([tuple(r) for r in rows], [
            (40.0, 40.0, "kg", "pending"),
            (20.0, 20.0, "kg", "pending"),
        ])
        # 迁移幂等：再跑一次不报错、不重复改数
        run_migrations(eng)
        os.unlink(legacy.name)

    def test_backfill_legacy_kg_without_product_goes_review_then_converts(self):
        db = SessionLocal()
        db.add(FeedingRecord(
            batch_id=self._batch_id, feeding_date=date(2026, 3, 1),
            feed_type="老牌子饲料", quantity=40, unit="kg",
            conversion_status=CONVERSION_PENDING,
        ))
        db.commit()
        db.close()

        r = client.post("/api/reconciliation/backfill/", json={"limit": 10})
        self.assertEqual(r.json()["batch"]["review_count"], 1)
        review = client.get("/api/reconciliation/review/").json()
        self.assertEqual(review[0]["review_reason"], "missing_spec")

        # 补齐产品规格（不猜比例）：规格与重算在同一事务，待复核记录立即恢复换算
        p = create_product("老牌子饲料", package_kg=40.0,
                           effective_from=date(2026, 1, 1))
        review_after = client.get("/api/reconciliation/review/").json()
        self.assertEqual(review_after, [])
        # 再显式重算是幂等空操作，不会重复换算
        db = SessionLocal()
        stats = conversion.recompute_affected(db, product_ids=[p["id"]])
        db.commit()
        db.close()
        self.assertEqual(stats["converted"], 0)
        cycle = client.get(f"/api/analysis/cycle/{self._batch_id}/").json()
        self.assertEqual(cycle["feed_total"], 40.0)

    def setUp(self):
        reset_db()
        db = SessionLocal()
        _, batch = make_pond_batch(db)
        db.commit()
        self._batch_id = batch.id
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
