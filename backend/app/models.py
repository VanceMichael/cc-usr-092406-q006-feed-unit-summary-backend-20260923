from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Boolean, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Pond(Base):
    __tablename__ = "ponds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    area = Column(Float, nullable=False, comment="面积(亩)")
    water_depth = Column(Float, nullable=False, comment="水深(米)")
    species = Column(String(100), comment="养殖品种")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batches = relationship("Batch", back_populates="pond")


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String(50), unique=True, index=True, nullable=False, comment="批次号")
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="养殖品种")
    stocking_date = Column(Date, nullable=False, comment="放苗日期")
    estimated_harvest_date = Column(Date, comment="预计收获日期")
    actual_harvest_date = Column(Date, comment="实际收获日期")
    status = Column(String(20), default="active", comment="状态: active, harvested, closed")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pond = relationship("Pond", back_populates="batches")
    stocking_records = relationship("StockingRecord", back_populates="batch")
    feeding_records = relationship("FeedingRecord", back_populates="batch")
    water_quality_records = relationship("WaterQualityRecord", back_populates="batch")
    medication_records = relationship("MedicationRecord", back_populates="batch")
    cost_records = relationship("CostRecord", back_populates="batch")
    harvest_sales = relationship("HarvestSale", back_populates="batch")


class StockingRecord(Base):
    __tablename__ = "stocking_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="品种")
    quantity = Column(Integer, nullable=False, comment="数量(尾)")
    source = Column(String(200), comment="来源")
    batch_number = Column(String(50), comment="苗种批次号")
    weight_per_unit = Column(Float, comment="单重(克/尾)")
    total_weight = Column(Float, comment="总重量(公斤)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="stocking_records")


# ---------------------------------------------------------------------------
# 饲料产品与包装规格（版本化，生效期明确，同一产品生效区间不允许交叠）
# ---------------------------------------------------------------------------

class FeedProduct(Base):
    """饲料产品：现场使用过的全名/简称/批号别名统一登记到一个产品下。"""
    __tablename__ = "feed_products"

    id = Column(Integer, primary_key=True, index=True)
    canonical_name = Column(String(200), unique=True, nullable=False, comment="产品标准名")
    status = Column(String(20), default="active", comment="状态: active, merged")
    merged_into_id = Column(Integer, ForeignKey("feed_products.id"), nullable=True, comment="同名裁定合并目标")
    created_at = Column(DateTime, default=datetime.utcnow)

    aliases = relationship("FeedProductAlias", back_populates="product", cascade="all, delete-orphan")
    versions = relationship("FeedProductVersion", back_populates="product", cascade="all, delete-orphan")
    measurements = relationship("FeedingMeasurement", back_populates="product")


class FeedProductAlias(Base):
    """产品别名：全名、简称、生产批号的归并依据；别名全局唯一，指向两个产品即冲突。"""
    __tablename__ = "feed_product_aliases"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False)
    alias = Column(String(200), unique=True, nullable=False, index=True, comment="别名/全名/批号")
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("FeedProduct", back_populates="aliases")


class FeedProductVersion(Base):
    """
    包装规格版本。投料包装单位固定为“袋”，每袋净重 kg_per_bag 公斤。
    生效期 [effective_from, effective_to) 左闭右开；effective_to 为空表示最新版。
    """
    __tablename__ = "feed_product_versions"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, comment="版本号, 从1递增")
    package_label = Column(String(200), comment="包装标注, 如 20kg/袋")
    kg_per_bag = Column(Float, nullable=False, comment="每袋净重(公斤)")
    effective_from = Column(Date, nullable=False, comment="生效日(含)")
    effective_to = Column(Date, nullable=True, comment="失效日(不含); 空为最新版")
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("FeedProduct", back_populates="versions")
    measurements = relationship("FeedingMeasurement", back_populates="spec_version")

    __table_args__ = (
        Index("ix_feed_versions_product_dates", "product_id", "effective_from", "effective_to"),
        # 并发裁定时同一产品同一天只能有一版生效，数据库层拒绝第二个提交者
        UniqueConstraint("product_id", "effective_from", name="uq_feed_version_product_from"),
    )


# ---------------------------------------------------------------------------
# 投喂记录 + 计量结果
# ---------------------------------------------------------------------------

class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    feeding_date = Column(Date, nullable=False, comment="投喂日期")
    feed_type = Column(String(200), nullable=False, comment="饲料类型(原文, 可能混有全名/简称/批号)")
    feed_quantity = Column(Float, nullable=True, comment="投喂量公斤(旧字段, 仅兼容历史数据)")
    # 原始计量三要素：现场登记的数量、单位与当时包装
    raw_quantity = Column(Float, nullable=True, comment="原始数量")
    raw_unit = Column(String(10), nullable=True, comment="原始单位: bag/g/kg")
    package_label = Column(String(200), nullable=True, comment="当时包装标注(原文)")
    signed_off = Column(Boolean, default=False, nullable=False, comment="是否已签署锁定")
    signed_off_at = Column(DateTime, nullable=True)
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="feeding_records")
    measurement = relationship(
        "FeedingMeasurement", back_populates="record", uselist=False, cascade="all, delete-orphan"
    )


class FeedingMeasurement(Base):
    """
    单条投喂记录的计量结果，与投喂记录 1:1。
    status=ok 时 quantity_kg 可计入汇总；status=review 时进复核队列，任何汇总不得计入。
    """
    __tablename__ = "feeding_measurements"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=False, unique=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=True, index=True)
    spec_version_id = Column(Integer, ForeignKey("feed_product_versions.id"), nullable=True, index=True)
    raw_quantity = Column(Float, nullable=False, comment="换算采用的原始数量")
    raw_unit = Column(String(10), nullable=False, comment="换算采用的原始单位")
    package_label = Column(String(200), nullable=True, comment="换算采用的包装原文")
    quantity_kg = Column(Float, nullable=True, comment="按规定精度换算后的公斤数")
    status = Column(String(10), nullable=False, default="review", comment="ok/review")
    review_reason = Column(String(50), nullable=True, comment="待复核原因码")
    resolved_product_name = Column(String(200), nullable=True, comment="解析出的产品名")
    spec_effective_from = Column(Date, nullable=True, comment="采用规格的生效日")
    spec_version_no = Column(Integer, nullable=True, comment="采用规格的版本号")
    spec_kg_per_bag = Column(Float, nullable=True, comment="换算时采用的每袋净重快照(公斤)")
    calculated_at = Column(DateTime, default=datetime.utcnow, comment="本次换算时间")
    calc_token = Column(String(64), nullable=True, index=True, comment="防重复换算令牌(记录指纹)")

    record = relationship("FeedingRecord", back_populates="measurement")
    product = relationship("FeedProduct", back_populates="measurements")
    spec_version = relationship("FeedProductVersion", back_populates="measurements")


class ReviewItem(Base):
    """复核队列：规格缺失、同名/别名冲突、单位非法等无法自动换算的记录。"""
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=False, index=True)
    reason = Column(String(50), nullable=False, comment="原因码")
    detail = Column(Text, nullable=True)
    candidates_json = Column(Text, nullable=True, comment="候选规格JSON")
    status = Column(String(10), nullable=False, default="open", comment="open/resolved/rejected")
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    record = relationship("FeedingRecord")


class RecalcJob(Base):
    """分批回算任务：last_id 水位支持中断续跑；calc_token 保证同批/重跑不重复换算。"""
    __tablename__ = "recalc_jobs"

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(20), nullable=False, default="all", comment="all/spec_change")
    params_json = Column(Text, nullable=True, comment="任务参数JSON")
    status = Column(String(10), nullable=False, default="pending", comment="pending/running/paused/done/failed")
    total = Column(Integer, default=0, comment="候选记录快照数")
    processed = Column(Integer, default=0, comment="实际换算条数")
    unchanged = Column(Integer, default=0, comment="指纹未变跳过换算的条数")
    to_review = Column(Integer, default=0, comment="本次进入复核条数")
    skipped_signed = Column(Integer, default=0, comment="跳过的已签署记录数")
    last_id = Column(Integer, default=0, comment="续跑水位: 已扫描最大记录ID")
    heartbeat_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class WaterQualityRecord(Base):
    __tablename__ = "water_quality_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    record_date = Column(Date, nullable=False, comment="检测日期")
    record_time = Column(String(20), comment="检测时间")
    water_temperature = Column(Float, comment="水温(℃)")
    ph_value = Column(Float, comment="pH值")
    dissolved_oxygen = Column(Float, comment="溶解氧(mg/L)")
    ammonia_nitrogen = Column(Float, comment="氨氮(mg/L)")
    nitrite = Column(Float, comment="亚硝酸盐(mg/L)")
    transparency = Column(Float, comment="透明度(cm)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="water_quality_records")


class MedicationRecord(Base):
    __tablename__ = "medication_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    medication_date = Column(Date, nullable=False, comment="用药日期")
    drug_name = Column(String(200), nullable=False, comment="药品名称")
    drug_type = Column(String(50), comment="药品类型")
    dosage = Column(Float, comment="用量")
    dosage_unit = Column(String(20), default="kg", comment="用量单位")
    administration_method = Column(String(100), comment="施用方法")
    purpose = Column(String(200), comment="用途")
    manufacturer = Column(String(200), comment="生产厂家")
    batch_number = Column(String(50), comment="药品批次号")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="medication_records")


class CostRecord(Base):
    __tablename__ = "cost_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    cost_date = Column(Date, nullable=False, comment="费用日期")
    cost_type = Column(String(50), nullable=False, comment="费用类型: feed, medicine, labor, electricity, other")
    amount = Column(Float, nullable=False, comment="金额(元)")
    description = Column(String(500), comment="费用描述")
    quantity = Column(Float, comment="数量")
    unit = Column(String(20), comment="单位")
    unit_price = Column(Float, comment="单价")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="cost_records")


class HarvestSale(Base):
    __tablename__ = "harvest_sales"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    sale_date = Column(Date, nullable=False, comment="销售日期")
    weight = Column(Float, nullable=False, comment="重量(公斤)")
    unit_price = Column(Float, nullable=False, comment="单价(元/公斤)")
    total_amount = Column(Float, comment="总金额(元)")
    buyer = Column(String(200), comment="买家")
    batch_number = Column(String(50), comment="追溯批次号")
    quality_grade = Column(String(50), comment="质量等级")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="harvest_sales")
