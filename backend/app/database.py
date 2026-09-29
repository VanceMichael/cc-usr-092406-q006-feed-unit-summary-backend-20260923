from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from pathlib import Path

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./aquaculture.db"
)

if SQLALCHEMY_DATABASE_URL.startswith("sqlite:///"):
    db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "")
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 旧库（计量模块上线前的表）需要补列；create_all 不会改既有表。
# 按 ORM 模型与实际表的列差集自动补列，只做无损 ADD COLUMN，绝不猜测或改写历史数值。
def _ensure_legacy_columns(db_engine) -> None:
    inspector = inspect(db_engine)
    existing_tables = set(inspector.get_table_names())
    with db_engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                col_type_sql = column.type.compile(dialect=db_engine.dialect)
                suffix = ""
                default_arg = column.default.arg if column.default is not None else None
                if default_arg is not None:
                    if isinstance(default_arg, bool):
                        literal = "1" if default_arg else "0"
                    elif isinstance(default_arg, str):
                        literal = "'{}'".format(default_arg.replace("'", "''"))
                    else:
                        literal = str(default_arg)
                    suffix = f" DEFAULT {literal} NOT NULL" if not column.nullable else f" DEFAULT {literal}"
                # 旧表新增 NOT NULL 但无默认的列会失败：退为可空，历史行给 NULL（语义=未登记）
                conn.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type_sql}{suffix}")
                )


def init_db() -> None:
    from . import models  # noqa: F401  确保模型已注册到 Base.metadata
    Base.metadata.create_all(bind=engine)
    _ensure_legacy_columns(engine)
