"""计量链测试公共夹具：每个用例独立临时 SQLite 库。"""

import os
import tempfile
import unittest

_TMP_DB = os.path.join(tempfile.gettempdir(), "aqua_metrology_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.database import Base, engine, SessionLocal  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.services import specs as spec_service  # noqa: E402


class MetrologyDBTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        # 清空所有表数据但保留结构，保证用例间完全隔离
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
        self.db = SessionLocal()
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    # -- 造数便捷方法 ------------------------------------------------------

    def create_pond_batch(self, batch_number="B2026-01", stocking_date=None, pond_name="1号塘"):
        from datetime import date
        pond = models.Pond(name=pond_name, area=10.0, water_depth=1.8, species="草鱼")
        self.db.add(pond)
        self.db.flush()
        batch = models.Batch(
            batch_number=batch_number, pond_id=pond.id, species="草鱼",
            stocking_date=stocking_date or date(2026, 1, 1),
        )
        self.db.add(batch)
        self.db.flush()
        return pond, batch

    def create_product_with_versions(self, name="通威103成鱼料", aliases=None, versions=None):
        """versions: [(kg_per_bag, effective_from, label), ...] 按时间顺序。"""
        from datetime import date
        product = spec_service.create_product(
            self.db, name, aliases or ["通威103", "103料"]
        )
        versions = versions or [
            (40.0, date(2026, 1, 1), "40kg/袋"),
            (20.0, date(2026, 4, 1), "20kg/袋"),
        ]
        created = []
        for kg, eff, label in versions:
            created.append(spec_service.add_version(self.db, product.id, kg, eff, label))
        self.db.commit()
        return product, created

    def add_feeding(self, batch, feeding_date, feed_type, raw_quantity=None, raw_unit=None,
                    package_label=None, legacy_feed_quantity=None, measure=True):
        record = models.FeedingRecord(
            batch_id=batch.id,
            feeding_date=feeding_date,
            feed_type=feed_type,
            raw_quantity=raw_quantity,
            raw_unit=raw_unit,
            package_label=package_label,
            feed_quantity=legacy_feed_quantity,
        )
        self.db.add(record)
        self.db.flush()
        if measure:
            from backend.app.services.feeding import calculate_for_record
            calculate_for_record(self.db, record)
        self.db.commit()
        self.db.refresh(record)
        return record
