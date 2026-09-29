from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime

# 换算状态常量（与 models 对齐，供 schema 文档引用）
CONVERSION_PENDING = "pending"
CONVERSION_CONVERTED = "converted"
CONVERSION_REVIEW = "review"

class PondBase(BaseModel):
    name: str
    area: float
    water_depth: float
    species: Optional[str] = None
    status: Optional[str] = "active"

class PondCreate(PondBase):
    pass

class PondUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None
    species: Optional[str] = None
    status: Optional[str] = None

class PondResponse(PondBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class BatchBase(BaseModel):
    batch_number: str
    pond_id: int
    species: str
    stocking_date: date
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = "active"

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    pond_id: Optional[int] = None
    species: Optional[str] = None
    stocking_date: Optional[date] = None
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = None

class BatchResponse(BatchBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class StockingRecordBase(BaseModel):
    batch_id: int
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordCreate(StockingRecordBase):
    pass

class StockingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    species: Optional[str] = None
    quantity: Optional[int] = None
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordResponse(StockingRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class FeedingRecordBase(BaseModel):
    batch_id: int
    feeding_date: date
    feed_type: str  # 现场原始文本（可能混有全名/简称/批号）
    # 原始计量：保留原始数量与原始单位；袋计量可登记"当时包装"
    quantity: float
    unit: str  # bag / g / kg（支持中文"袋/克/公斤"）
    package_kg: Optional[float] = None
    package_batch_no: Optional[str] = None
    resolved_product_id: Optional[int] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordCreate(FeedingRecordBase):
    pass

class FeedingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    feeding_date: Optional[date] = None
    feed_type: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    package_kg: Optional[float] = None
    package_batch_no: Optional[str] = None
    resolved_product_id: Optional[int] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordResponse(BaseModel):
    id: int
    batch_id: int
    feeding_date: date
    feed_type: str
    # 原始计量
    quantity: Optional[float] = None
    unit: Optional[str] = None
    package_kg: Optional[float] = None
    package_batch_no: Optional[str] = None
    # 裁定与换算结果
    resolved_product_id: Optional[int] = None
    product_id: Optional[int] = None
    spec_version_id: Optional[int] = None
    resolved_package_kg: Optional[float] = None
    feed_quantity: Optional[float] = None
    conversion_status: Optional[str] = None
    review_reason: Optional[str] = None
    conversion_batch_id: Optional[int] = None
    converted_at: Optional[datetime] = None
    signed_at: Optional[datetime] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True

# ---------------------------------------------------------------------------
# 饲料产品与规格版本
# ---------------------------------------------------------------------------

class FeedVersionBase(BaseModel):
    package_kg: float
    effective_from: date
    effective_to: Optional[date] = None
    change_reason: Optional[str] = None

class FeedVersionCreate(FeedVersionBase):
    pass

class FeedVersionResponse(FeedVersionBase):
    id: int
    product_id: int
    version_number: int
    created_at: datetime

    class Config:
        orm_mode = True

class FeedProductBase(BaseModel):
    full_name: str
    short_name: Optional[str] = None
    aliases: Optional[str] = None

class FeedProductCreate(FeedProductBase):
    # 新建产品时可同时给首版规格（package_kg+effective_from）
    package_kg: Optional[float] = None
    effective_from: Optional[date] = None
    change_reason: Optional[str] = None

class FeedProductUpdate(BaseModel):
    full_name: Optional[str] = None
    short_name: Optional[str] = None
    aliases: Optional[str] = None

class FeedProductResponse(FeedProductBase):
    id: int
    status: str
    merged_into_id: Optional[int] = None
    versions: List[FeedVersionResponse] = []
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True

class FeedProductMergeRequest(BaseModel):
    # 把 source_id 并入 target_id（同名裁定）
    source_id: int
    target_id: int
    effective_from: Optional[date] = None
    date_to: Optional[date] = None

class PackageCorrectionRequest(BaseModel):
    # 包装更正：通过新增版本实现，只重算未签署且落在生效区间的记录
    product_id: int
    package_kg: float
    effective_from: date
    effective_to: Optional[date] = None
    change_reason: Optional[str] = None

# ---------------------------------------------------------------------------
# 签署 / 复核 / 换算批次
# ---------------------------------------------------------------------------

class SignRequest(BaseModel):
    record_ids: Optional[List[int]] = None  # 空表示签署指定批次全部已换算记录

class SignResult(BaseModel):
    signed: int
    skipped_unsigned_or_review: int

class ReviewResolveRequest(BaseModel):
    # 人工复核：显式指定产品（可选，配合修正原始计量），随后重新换算
    product_id: Optional[int] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    package_kg: Optional[float] = None
    package_batch_no: Optional[str] = None
    feed_type: Optional[str] = None
    feeding_date: Optional[date] = None

class BackfillRequest(BaseModel):
    limit: int = 200
    interrupt: bool = False

class ConversionBatchResponse(BaseModel):
    id: int
    status: str
    total_seen: int
    converted_count: int
    review_count: int
    last_record_id: int
    last_run_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        orm_mode = True

class BackfillResult(BaseModel):
    batch: ConversionBatchResponse
    processed_in_run: int
    pending_remaining: int

# ---------------------------------------------------------------------------
# 差额核对：饲料仓按袋核出的出库量 vs 养殖分析公斤数，可逐笔回到原记录
# ---------------------------------------------------------------------------

class VarianceRecordItem(BaseModel):
    record_id: int
    batch_id: int
    feeding_date: date
    feed_type: str
    original_quantity: Optional[float] = None
    original_unit: Optional[str] = None
    record_package_kg: Optional[float] = None
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    spec_version_id: Optional[int] = None
    spec_version_number: Optional[int] = None
    spec_package_kg: Optional[float] = None
    quantity_kg: Optional[float] = None
    conversion_status: str
    review_reason: Optional[str] = None
    signed: bool = False

class VarianceReport(BaseModel):
    product_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    # 饲料仓口径
    warehouse_bags: Optional[float] = None
    warehouse_package_kg: Optional[float] = None
    warehouse_kg: Optional[float] = None
    # 养殖分析口径（仅 converted）
    converted_kg: float
    converted_count: int
    bags_consumed: float  # 换算过程中按袋计量折算的袋数合计（原始袋数，供与仓库对数）
    # 差额
    variance_kg: Optional[float] = None
    # 隔离项：不进入公斤合计，但必须显式列出
    pending_records: List[VarianceRecordItem] = []
    review_records: List[VarianceRecordItem] = []
    converted_records: List[VarianceRecordItem] = []

class WaterQualityRecordBase(BaseModel):
    batch_id: int
    record_date: date
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordCreate(WaterQualityRecordBase):
    pass

class WaterQualityRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    record_date: Optional[date] = None
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordResponse(WaterQualityRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class MedicationRecordBase(BaseModel):
    batch_id: int
    medication_date: date
    drug_name: str
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = "kg"
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordCreate(MedicationRecordBase):
    pass

class MedicationRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    medication_date: Optional[date] = None
    drug_name: Optional[str] = None
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = None
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordResponse(MedicationRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class CostRecordBase(BaseModel):
    batch_id: int
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordCreate(CostRecordBase):
    pass

class CostRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    cost_date: Optional[date] = None
    cost_type: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordResponse(CostRecordBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class HarvestSaleBase(BaseModel):
    batch_id: int
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleCreate(HarvestSaleBase):
    pass

class HarvestSaleUpdate(BaseModel):
    batch_id: Optional[int] = None
    sale_date: Optional[date] = None
    weight: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleResponse(HarvestSaleBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True

class CostSummaryItem(BaseModel):
    type: str
    amount: float

class FeedingSummaryItem(BaseModel):
    feed_type: str
    total_quantity: float
    feeding_count: int

class FeedingSummaryByProductItem(BaseModel):
    # 逐产品、逐规格版本的汇总行（周期分析逐笔说明的聚合视图）
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    spec_version_id: Optional[int] = None
    spec_version_number: Optional[int] = None
    package_kg: Optional[float] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    converted_kg: float = 0.0
    record_count: int = 0
    pending_count: int = 0
    review_count: int = 0

class FeedingSummary(BaseModel):
    # 只有 converted 公斤进入总计；pending/review 单独披露、不被当作 0
    total_feed_weight: float
    feeding_count: int
    avg_daily_feed: float
    converted_count: int
    pending_count: int
    review_count: int
    by_product_version: List[FeedingSummaryByProductItem] = []

class CultureCycleAnalysis(BaseModel):
    batch_number: str
    pond_name: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    days_cultured: Optional[int] = None
    initial_quantity: int
    harvest_weight: float
    survival_rate: float
    feed_total: float
    feed_conversion_ratio: float
    area: float
    yield_per_mu: float
    total_cost: float
    total_revenue: float
    profit: float
    cost_summary: Optional[dict] = None
    feeding_summary: Optional[FeedingSummary] = None

class StockingRecordTrace(BaseModel):
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    stocking_date: Optional[date] = None

class FeedingRecordTrace(BaseModel):
    record_id: int
    feeding_date: date
    feed_type: str
    # 原始计量
    original_quantity: Optional[float] = None
    original_unit: Optional[str] = None
    record_package_kg: Optional[float] = None  # 记录登记的当时包装
    package_batch_no: Optional[str] = None
    # 采用的规格版本（逐笔说明）
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    spec_version_id: Optional[int] = None
    spec_version_number: Optional[int] = None
    spec_package_kg: Optional[float] = None  # 换算时冻结采用的袋规格
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    # 结果
    quantity_kg: Optional[float] = None
    conversion_status: str
    review_reason: Optional[str] = None
    signed: bool = False

class WaterQualityRecordTrace(BaseModel):
    record_date: date
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None

class MedicationRecordTrace(BaseModel):
    medication_date: date
    medication_name: str
    dosage: Optional[float] = None
    unit: Optional[str] = None

class CostRecordTrace(BaseModel):
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None

class HarvestSaleTrace(BaseModel):
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None

class BatchInfo(BaseModel):
    batch_number: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    status: str
    pond_id: Optional[int] = None

class PondInfo(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None

class BatchTraceability(BaseModel):
    batch: BatchInfo
    pond_info: PondInfo
    stocking_records: List[StockingRecordTrace] = []
    feeding_records: List[FeedingRecordTrace] = []
    water_quality_records: List[WaterQualityRecordTrace] = []
    medication_records: List[MedicationRecordTrace] = []
    cost_records: List[CostRecordTrace] = []
    harvest_sales: List[HarvestSaleTrace] = []
