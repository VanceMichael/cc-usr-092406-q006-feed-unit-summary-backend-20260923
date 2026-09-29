"""投喂计量链核心：解析混写的饲料字段 -> 产品/别名归并 -> 按生效期选规格版本 -> 换算公斤。

规则：
- 现场把全名、简称、生产批号混写在 feed_type 时，先拆词，再按 别名/标准名/批号 归并到产品；
- 同一文本能归并到多个产品 = 同名冲突，进复核，禁止猜测；
- 规格按投喂日期落在 [effective_from, effective_to) 选取；缺失进复核；
- 换算结果写 FeedingMeasurement（与投喂记录 1:1），待复核记录绝不进入任何汇总。
"""

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import (
    FeedProduct,
    FeedProductAlias,
    FeedProductVersion,
    FeedingMeasurement,
    FeedingRecord,
    ReviewItem,
)
from .units import (
    InvalidUnitError,
    SpecRequiredError,
    normalize_unit,
    to_kilograms,
)

# 复核原因码
REVIEW_MISSING_PRODUCT = "missing_product"          # 饲料名归并不到任何产品
REVIEW_MISSING_VERSION = "missing_spec_version"     # 产品存在但投喂日期无生效规格
REVIEW_NAME_CONFLICT = "product_name_conflict"      # 同名/别名指向多个产品
REVIEW_INVALID_UNIT = "invalid_unit"                # 原始单位无法识别
REVIEW_INVALID_QUANTITY = "invalid_quantity"        # 原始数量缺失/非法
REVIEW_BAG_NO_SPEC = "bag_without_spec"             # 按袋登记但缺每袋净重

_LOT_PATTERNS = [
    re.compile(r"(?:生产批号|批号|批次号?|lot\.?\s*no\.?|lot|no\.?)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})", re.IGNORECASE),
]
_PACKAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?\s*(?:kg|公斤|千克)\s*[/／每]?\s*[袋包])", re.IGNORECASE)
_PURE_LOT = re.compile(r"^[A-Za-z]{0,3}\d{6,}[-A-Za-z0-9]*$")
_SEPARATORS = re.compile(r"[\s,，;；|]+")


def parse_feed_text(raw: str) -> dict:
    """
    拆解混写的饲料类型字段。
    返回 {"names": [可能的名称片段], "lot": 批号或None, "package": 包装标注或None, "raw": 原文}
    """
    text = (raw or "").strip()
    lot = None
    package = None

    pkg_match = _PACKAGE_PATTERN.search(text)
    if pkg_match:
        package = pkg_match.group(1).strip()
        text = text[:pkg_match.start()] + " " + text[pkg_match.end():]

    for pat in _LOT_PATTERNS:
        m = pat.search(text)
        if m:
            lot = m.group(1).strip()
            text = text[:m.start()] + " " + text[m.end():]
            break

    # 圆括号/书名号里的片段单独成词
    bracketed = re.findall(r"[（(【\[]([^（）()【】\[\]]+)[）)】\]]", text)
    text = re.sub(r"[（(【\[][^（）()【】\[\]]+[）)】\]]", " ", text)

    candidates = []
    for piece in _SEPARATORS.split(text) + bracketed:
        piece = piece.strip(" -")
        if not piece:
            continue
        if lot is None and _PURE_LOT.match(piece):
            lot = piece
            continue
        candidates.append(piece)

    # 整段原文本身永远是第一候选（精确匹配优先于拆词）
    normalized = re.sub(r"\s+", " ", (raw or "").strip())
    ordered = []
    if normalized:
        ordered.append(normalized)
    for c in candidates:
        if c not in ordered:
            ordered.append(c)
    return {"names": ordered, "lot": lot, "package": package, "raw": normalized}


def _resolve_product(db: Session, parsed: dict) -> Tuple[Optional[FeedProduct], Optional[str], Optional[dict]]:
    """
    返回 (product, reason, detail)。
    product 为 None 时 reason 说明是缺失还是冲突；冲突时 detail 给出候选产品。
    """
    keys = list(parsed["names"])
    if parsed["lot"]:
        keys.append(parsed["lot"])

    matched_product_ids = set()
    alias_hits = {}
    for key in keys:
        norm = key.strip()
        if not norm:
            continue
        rows = db.query(FeedProductAlias).filter(FeedProductAlias.alias == norm).all()
        for row in rows:
            matched_product_ids.add(row.product_id)
            alias_hits.setdefault(row.product_id, norm)
        canon = db.query(FeedProduct).filter(FeedProduct.canonical_name == norm).all()
        for row in canon:
            matched_product_ids.add(row.id)
            alias_hits.setdefault(row.id, norm)

    if len(matched_product_ids) > 1:
        products = db.query(FeedProduct).filter(FeedProduct.id.in_(matched_product_ids)).all()
        detail = {
            "matched_keys": sorted(set(alias_hits.values())),
            "candidates": [
                {"product_id": p.id, "canonical_name": p.canonical_name, "status": p.status}
                for p in products
            ],
        }
        return None, REVIEW_NAME_CONFLICT, detail
    if len(matched_product_ids) == 1:
        pid = next(iter(matched_product_ids))
        product = db.get(FeedProduct, pid)
        # 合并产品跟随到裁定后的主产品
        if product and product.status == "merged" and product.merged_into_id:
            product = db.get(FeedProduct, product.merged_into_id)
        return product, None, None

    return None, REVIEW_MISSING_PRODUCT, {"matched_keys": keys}


def find_effective_version(db: Session, product_id: int, on_date) -> Optional[FeedProductVersion]:
    """按投喂日期选取生效规格版本：effective_from <= 日期 < effective_to（NULL 视为开放）。"""
    versions = db.query(FeedProductVersion).filter(
        FeedProductVersion.product_id == product_id,
        FeedProductVersion.effective_from <= on_date,
        or_(FeedProductVersion.effective_to.is_(None), FeedProductVersion.effective_to > on_date),
    ).all()
    if not versions:
        return None
    # 数据层已禁止区间交叠；防御性地取版本号最大者，保证并发新建版本时裁定确定。
    return max(versions, key=lambda v: (v.version, v.id))


def _num_str(value) -> str:
    """数值规范化：整数 2 与浮点 2.0（SQLite 回读）必须得到同一指纹。"""
    if value is None or value == "":
        return ""
    return format(Decimal(str(value)).normalize(), "f")


def _fingerprint(record: FeedingRecord, spec_version_id, status: str, kg_per_bag=None) -> str:
    seed = "|".join([
        str(record.feed_type or ""),
        _num_str(record.raw_quantity),
        str(record.raw_unit or ""),
        str(record.package_label or ""),
        str(spec_version_id or ""),
        _num_str(kg_per_bag),
        status,
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _upsert_review_item(db: Session, record_id: int, reason: str, detail: Optional[dict]) -> None:
    open_item = db.query(ReviewItem).filter(
        ReviewItem.record_id == record_id,
        ReviewItem.status == "open",
    ).first()
    payload = json.dumps(detail, ensure_ascii=False, default=str) if detail else None
    if open_item:
        open_item.reason = reason
        open_item.candidates_json = payload
        return
    db.add(ReviewItem(record_id=record_id, reason=reason, candidates_json=payload, status="open"))


def _close_open_reviews(db: Session, record_id: int) -> None:
    for item in db.query(ReviewItem).filter(
        ReviewItem.record_id == record_id, ReviewItem.status == "open"
    ).all():
        item.status = "resolved"
        item.resolved_at = datetime.utcnow()
        item.resolution_note = "规格齐备, 自动换算成功"


def calculate_for_record(db: Session, record: FeedingRecord, *, force: bool = False):
    """
    对单条投喂记录执行完整计量链并落库（幂等）。
    返回 (measurement, changed)：指纹未变复用既有结果时 changed=False（防止重复换算）。
    force=True 时无视指纹重算；已签署记录仍然锁定，永不重算。
    """
    existing = db.query(FeedingMeasurement).filter(
        FeedingMeasurement.record_id == record.id
    ).first()

    if existing and record.signed_off:
        return existing, False

    # 旧数据兼容：计量模块上线前只有 feed_quantity（系统当时按公斤登记），
    # 原样回填为原始数量，公斤->公斤不涉及任何比例猜测。
    if record.raw_quantity is None and record.feed_quantity is not None:
        record.raw_quantity = record.feed_quantity
        record.raw_unit = record.raw_unit or "kg"
        db.flush()

    parsed = parse_feed_text(record.feed_type)

    # 单位与数量先校验，避免用猜测值换算
    unit = normalize_unit(record.raw_unit)
    if record.raw_quantity is None:
        result_status, reason, detail = "review", REVIEW_INVALID_QUANTITY, {"raw_quantity": None}
        product, version = None, None
    elif unit is None:
        result_status, reason, detail = "review", REVIEW_INVALID_UNIT, {"raw_unit": record.raw_unit}
        product, version = None, None
    else:
        product, conflict_reason, detail = _resolve_product(db, parsed)
        version = None
        reason = None
        result_status = "ok"
        if conflict_reason == REVIEW_NAME_CONFLICT:
            result_status, reason = "review", REVIEW_NAME_CONFLICT
        elif product is None:
            result_status, reason = "review", REVIEW_MISSING_PRODUCT
        else:
            version = find_effective_version(db, product.id, record.feeding_date)
            if version is None:
                result_status, reason = "review", REVIEW_MISSING_VERSION
                detail = {"product_id": product.id, "feeding_date": str(record.feeding_date)}

    kg_value = None
    if result_status == "ok":
        try:
            kg_per_bag = version.kg_per_bag if unit == "bag" else None
            kg_value = to_kilograms(record.raw_quantity, unit, kg_per_bag)
        except SpecRequiredError:
            result_status, reason = "review", REVIEW_BAG_NO_SPEC
        except (InvalidUnitError, ValueError):
            result_status, reason = "review", REVIEW_INVALID_QUANTITY

    spec_version_id = version.id if (result_status == "ok" and version) else None
    bag_weight = version.kg_per_bag if (result_status == "ok" and version) else None
    token = _fingerprint(record, spec_version_id, result_status, bag_weight)

    if existing and not force and existing.calc_token == token:
        return existing, False

    if existing:
        measurement = existing
    else:
        measurement = FeedingMeasurement(record_id=record.id)
        db.add(measurement)

    measurement.product_id = product.id if product else None
    measurement.spec_version_id = spec_version_id
    measurement.raw_quantity = record.raw_quantity if record.raw_quantity is not None else 0
    measurement.raw_unit = unit or (record.raw_unit or "")
    measurement.package_label = record.package_label or parsed.get("package")
    measurement.quantity_kg = kg_value
    measurement.status = result_status
    measurement.review_reason = reason
    measurement.resolved_product_name = (
        product.canonical_name if product else (parsed["names"][0] if parsed["names"] else None)
    )
    measurement.spec_effective_from = version.effective_from if version else None
    measurement.spec_version_no = version.version if version else None
    measurement.spec_kg_per_bag = version.kg_per_bag if version else None
    measurement.calculated_at = datetime.utcnow()
    measurement.calc_token = token

    if result_status == "review":
        _upsert_review_item(db, record.id, reason, detail)
    else:
        _close_open_reviews(db, record.id)

    db.flush()
    return measurement, True
