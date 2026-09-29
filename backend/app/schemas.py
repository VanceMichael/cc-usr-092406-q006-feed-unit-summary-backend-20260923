from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime

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
    feed_type: str
    # 新口径：原始数量 + 原始单位(bag/g/kg) + 当时包装标注
    raw_quantity: Optional[float] = None
    raw_unit: Optional[str] = None
    package_label: Optional[str] = None
    # 旧口径兼容：只给 feed_quantity 时按公斤受理
    feed_quantity: Optional[float] = None
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
    raw_quantity: Optional[float] = None
    raw_unit: Optional[str] = None
    package_label: Optional[str] = None
    feed_quantity: Optional[float] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class MeasurementBrief(BaseModel):
    status: str
    quantity_kg: Optional[float] = None
    review_reason: Optional[str] = None
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    spec_version_id: Optional[int] = None
    spec_version_no: Optional[int] = None
    spec_effective_from: Optional[date] = None
    package_label: Optional[str] = None
    calculated_at: Optional[datetime] = None

class FeedingRecordResponse(BaseModel):
    id: int
    batch_id: int
    feeding_date: date
    feed_type: str
    feed_quantity: Optional[float] = None
    raw_quantity: Optional[float] = None
    raw_unit: Optional[str] = None
    package_label: Optional[str] = None
    signed_off: bool = False
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime
    measurement: Optional[MeasurementBrief] = None

    class Config:
        from_attributes = True

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


# ---------------------------------------------------------------------------
# 产品规格与计量链
# ---------------------------------------------------------------------------

class FeedProductCreate(BaseModel):
    canonical_name: str
    aliases: Optional[List[str]] = None

class FeedProductResponse(BaseModel):
    id: int
    canonical_name: str
    status: str
    merged_into_id: Optional[int] = None
    aliases: List[str] = []
    created_at: datetime

    class Config:
        from_attributes = True

class FeedAliasCreate(BaseModel):
    alias: str

class FeedVersionCreate(BaseModel):
    kg_per_bag: float = Field(..., description="每袋净重(公斤), 必须为正")
    effective_from: date
    package_label: Optional[str] = None

class FeedVersionResponse(BaseModel):
    id: int
    product_id: int
    version: int
    package_label: Optional[str] = None
    kg_per_bag: float
    effective_from: date
    effective_to: Optional[date] = None
    created_at: datetime

    class Config:
        from_attributes = True

class FeedVersionCorrectRequest(BaseModel):
    kg_per_bag: Optional[float] = Field(None, description="更正后的每袋净重(公斤), 必须为正")
    package_label: Optional[str] = None

class SameNameAdjudicationRequest(BaseModel):
    source_product_id: int
    target_product_id: int

class SameNameAdjudicationResponse(BaseModel):
    merged_product_id: int
    target_product_id: int
    relocated_aliases: int
    recalculated_records: List[int]


class ReviewItemResponse(BaseModel):
    id: int
    record_id: int
    reason: str
    candidates_json: Optional[str] = None
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None

    class Config:
        from_attributes = True

class ReviewResolveRequest(BaseModel):
    # 人工复核结论：指明该记录归并到哪个产品（必须已存在）；
    # 系统只按该产品投喂日的生效规格重算，绝不接受人工猜测的换算比例。
    product_id: int
    note: Optional[str] = None


class RecalcJobResponse(BaseModel):
    id: int
    scope: str
    params: Optional[dict] = None
    status: str
    total: int
    processed: int
    unchanged: int
    to_review: int
    skipped_signed: int
    last_id: int
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

class RecalcJobCreateRequest(BaseModel):
    scope: str = "all"
    product_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    batch_size: int = 100


class FeedingBreakdownItem(BaseModel):
    feed_type: str
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    spec_version_no: Optional[int] = None
    total_kg: float
    feeding_count: int
    review_count: int

class VarianceLine(BaseModel):
    """单条记录对某口径差额的贡献，可回溯到原记录。"""
    record_id: int
    batch_id: int
    feeding_date: date
    feed_type: str
    raw_quantity: Optional[float] = None
    raw_unit: Optional[str] = None
    package_label: Optional[str] = None
    legacy_kg: Optional[float] = None
    measured_kg: Optional[float] = None
    diff_kg: float
    status: str
    review_reason: Optional[str] = None
    spec_version_no: Optional[int] = None
    signed_off: bool = False

class VarianceReport(BaseModel):
    batch_id: Optional[int] = None
    product_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    lines: List[VarianceLine] = []
    legacy_total_kg: float
    measured_total_kg: float
    excluded_review_count: int
    variance_kg: float
    note: str


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
    feeding_summary: Optional[dict] = None
    # 计量链口径：只统计 status=ok 的换算结果，待复核记录隔离单列
    feeding_breakdown: List[FeedingBreakdownItem] = []
    review_pending_count: int = 0
    feeding_record_count: int = 0
    measurement_note: Optional[str] = None

class StockingRecordTrace(BaseModel):
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    stocking_date: Optional[date] = None

class FeedingRecordTrace(BaseModel):
    feeding_date: date
    feed_type: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    # 原始计量三要素
    raw_quantity: Optional[float] = None
    raw_unit: Optional[str] = None
    package_label: Optional[str] = None
    # 换算结果与逐笔采用的规格版本
    quantity_kg: Optional[float] = None
    measurement_status: str = "review"
    review_reason: Optional[str] = None
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    spec_version_id: Optional[int] = None
    spec_version_no: Optional[int] = None
    spec_effective_from: Optional[date] = None
    spec_kg_per_bag: Optional[float] = None
    signed_off: bool = False

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
