from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

# 投喂记录换算状态
CONVERSION_PENDING = "pending"      # 尚未换算（旧数据/缺规格待回填）
CONVERSION_CONVERTED = "converted"  # 已按某一版规格换算成公斤
CONVERSION_REVIEW = "review"        # 进入人工复核（规格缺失/同名冲突等）

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

class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    feeding_date = Column(Date, nullable=False, comment="投喂日期")
    feed_type = Column(String(100), nullable=False, comment="现场填写的饲料类型原始文本(可能混有全名/简称/批号)")
    # 现场原始计量：每条记录必须保留原始数量、原始单位与当时包装
    quantity = Column(Float, nullable=True, comment="原始数量(按原始单位)")
    unit = Column(String(10), nullable=True, comment="原始单位: bag(袋)/g(克)/kg(公斤)")
    package_kg = Column(Float, nullable=True, comment="当时包装: 每袋公斤数(仅袋计量时填写,为当时现场包装)")
    package_batch_no = Column(String(100), nullable=True, comment="生产批号(从原始文本/现场拆出)")
    # 对应到生效期明确的产品规格版本
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=True, comment="裁定后的产品")
    resolved_product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=True, comment="人工复核显式裁定的产品(优先于名称解析)")
    spec_version_id = Column(Integer, ForeignKey("feed_product_versions.id"), nullable=True, comment="实际采用的规格版本")
    resolved_package_kg = Column(Float, nullable=True, comment="换算时实际采用的每袋公斤数快照(冻结,不受日后规格更正影响)")
    # 换算结果
    feed_quantity = Column(Float, nullable=True, comment="标准化投喂量(公斤),仅 converted 状态可用于汇总")
    conversion_status = Column(String(20), nullable=True, default=CONVERSION_PENDING, comment="pending/converted/review")
    review_reason = Column(String(200), nullable=True, comment="进入复核的原因: missing_spec/ambiguous_product/...")
    conversion_hash = Column(String(64), nullable=True, comment="换算输入哈希,用于幂等防重复换算")
    conversion_batch_id = Column(Integer, ForeignKey("conversion_batches.id"), nullable=True, comment="最近一次换算所属批次")
    converted_at = Column(DateTime, nullable=True)
    # 签署: 周期数据一旦签署,包装更正不得再改动
    signed_at = Column(DateTime, nullable=True, comment="签署时间; 已签署记录不参与重算")
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="feeding_records")
    product = relationship("FeedProduct", foreign_keys=[product_id])
    spec_version = relationship("FeedProductVersion", foreign_keys=[spec_version_id])

class FeedProduct(Base):
    """饲料产品: 同一产品可有全名与简称; 规格以"生效版本"管理,供应商更换包装即新增版本。"""
    __tablename__ = "feed_products"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(200), nullable=False, comment="产品全名")
    short_name = Column(String(100), nullable=True, comment="产品简称")
    aliases = Column(Text, nullable=True, comment="其他别名,逗号分隔")
    status = Column(String(20), default="active", comment="active/merged(已并入其他产品)")
    merged_into_id = Column(Integer, ForeignKey("feed_products.id"), nullable=True, comment="同名裁定合并目标")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = relationship(
        "FeedProductVersion", back_populates="product",
        foreign_keys="FeedProductVersion.product_id",
        order_by="FeedProductVersion.version_number",
    )
    merged_into = relationship("FeedProduct", remote_side=[id], foreign_keys=[merged_into_id])

class FeedProductVersion(Base):
    """产品规格版本: 生效期 [effective_from, effective_to), 袋规格 package_kg 仅对袋计量生效。"""
    __tablename__ = "feed_product_versions"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("feed_products.id"), nullable=False)
    version_number = Column(Integer, nullable=False, comment="版本号,同一产品内递增")
    package_kg = Column(Float, nullable=False, comment="本版包装: 每袋公斤数")
    effective_from = Column(Date, nullable=False, comment="生效起始日(含)")
    effective_to = Column(Date, nullable=True, comment="生效结束日(不含); 空表示至今")
    change_reason = Column(String(200), nullable=True, comment="变更原因,如供应商更换包装")
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("FeedProduct", back_populates="versions", foreign_keys=[product_id])

class ConversionBatch(Base):
    """旧记录分批换算批次: 可中断、可续跑、按记录状态防重复换算。"""
    __tablename__ = "conversion_batches"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), nullable=False, default="running", comment="running/interrupted/completed")
    total_seen = Column(Integer, default=0, comment="累计发现的待处理记录数")
    converted_count = Column(Integer, default=0, comment="累计换算成功数")
    review_count = Column(Integer, default=0, comment="累计进入复核数")
    last_record_id = Column(Integer, default=0, comment="续跑游标: 已处理到的最大记录ID")
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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
