"""轻量数据库迁移。

服务不引入 Alembic：启动时对 SQLite 做结构补齐。历史投喂记录的
feed_quantity 旧约束为 NOT NULL（旧逻辑无条件按公斤入库），而新计量链
要求"待复核记录不得有公斤数"，需要把该列改为可空 —— SQLite 不支持
直接修改列约束，因此检测到旧约束时按标准流程重建 feeding_records。
所有迁移幂等，可重复执行。
"""
from sqlalchemy import inspect, text

from .models import CONVERSION_PENDING

# feeding_records 上为计量链新增的列 -> 列定义
FEEDING_NEW_COLUMNS = {
    "quantity": "FLOAT",
    "unit": "VARCHAR(10)",
    "package_kg": "FLOAT",
    "package_batch_no": "VARCHAR(100)",
    "product_id": "INTEGER",
    "resolved_product_id": "INTEGER",
    "spec_version_id": "INTEGER",
    "resolved_package_kg": "FLOAT",
    "conversion_status": "VARCHAR(20)",
    "review_reason": "VARCHAR(200)",
    "conversion_hash": "VARCHAR(64)",
    "conversion_batch_id": "INTEGER",
    "converted_at": "DATETIME",
    "signed_at": "DATETIME",
}

# 重建后的目标表：feed_quantity 可空（待复核记录不产生公斤数）。
# 没有任何表通过外键指向 feeding_records，重建出向 FK 端无需关闭外键检查。
_FEEDING_TARGET_COLUMNS = [
    ("id", "INTEGER NOT NULL PRIMARY KEY"),
    ("batch_id", "INTEGER NOT NULL"),
    ("feeding_date", "DATE NOT NULL"),
    ("feed_type", "VARCHAR(100) NOT NULL"),
    ("quantity", "FLOAT"),
    ("unit", "VARCHAR(10)"),
    ("package_kg", "FLOAT"),
    ("package_batch_no", "VARCHAR(100)"),
    ("product_id", "INTEGER"),
    ("resolved_product_id", "INTEGER"),
    ("spec_version_id", "INTEGER"),
    ("resolved_package_kg", "FLOAT"),
    ("feed_quantity", "FLOAT"),
    ("conversion_status", "VARCHAR(20)"),
    ("review_reason", "VARCHAR(200)"),
    ("conversion_hash", "VARCHAR(64)"),
    ("conversion_batch_id", "INTEGER"),
    ("converted_at", "DATETIME"),
    ("signed_at", "DATETIME"),
    ("feeding_time", "VARCHAR(20)"),
    ("weather", "VARCHAR(50)"),
    ("water_temperature", "FLOAT"),
    ("notes", "TEXT"),
    ("created_at", "DATETIME"),
]


def _table_columns(conn, table: str):
    """PRAGMA table_info -> {列名: notnull(0/1)}。"""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: row[3] for row in rows}


def _rebuild_feeding_table(conn):
    """把 feeding_records 重建为含全部计量链列、feed_quantity 可空的结构。"""
    old_cols = set(_table_columns(conn, "feeding_records"))
    target_names = [name for name, _ in _FEEDING_TARGET_COLUMNS]

    col_defs = ", ".join(f"{name} {ddl}" for name, ddl in _FEEDING_TARGET_COLUMNS)
    conn.exec_driver_sql(
        "CREATE TABLE feeding_records_new (" + col_defs + ")"
    )

    # 仅拷贝旧表确实存在的列；新增列以 NULL 落库
    shared = [c for c in target_names if c in old_cols]
    shared_list = ", ".join(shared)
    conn.execute(text(
        f"INSERT INTO feeding_records_new ({shared_list}) "
        f"SELECT {shared_list} FROM feeding_records"
    ))
    conn.exec_driver_sql("DROP TABLE feeding_records")
    conn.exec_driver_sql("ALTER TABLE feeding_records_new RENAME TO feeding_records")


def run_migrations(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "feeding_records" not in tables:
        return  # 全新库由 Base.metadata.create_all 建表，无需迁移

    with engine.begin() as conn:
        cols = _table_columns(conn, "feeding_records")

        needs_rebuild = (
            # 旧约束：feed_quantity NOT NULL
            cols.get("feed_quantity") == 1
            # 或旧表缺少任何计量链新列
            or any(name not in cols for name in FEEDING_NEW_COLUMNS)
        )
        if needs_rebuild:
            _rebuild_feeding_table(conn)
            cols = _table_columns(conn, "feeding_records")

        # 兜底：理论上重建后列已齐全；若未来再增列仍可走 ADD COLUMN
        for name, ddl_type in FEEDING_NEW_COLUMNS.items():
            if name not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE feeding_records ADD COLUMN {name} {ddl_type}"
                )

        # 旧记录：feed_quantity 曾被无条件当作公斤。
        # 登记为原始数量(公斤)、单位 kg、待换算 —— 不猜测袋比例。
        conn.execute(
            text(
                "UPDATE feeding_records "
                "SET quantity = feed_quantity, unit = 'kg', conversion_status = :st "
                "WHERE quantity IS NULL AND feed_quantity IS NOT NULL"
            ),
            {"st": CONVERSION_PENDING},
        )
        # 极端情况下没有数量的脏数据也进入待处理，绝不以 0 参与汇总
        conn.execute(
            text(
                "UPDATE feeding_records SET conversion_status = :st "
                "WHERE conversion_status IS NULL"
            ),
            {"st": CONVERSION_PENDING},
        )
