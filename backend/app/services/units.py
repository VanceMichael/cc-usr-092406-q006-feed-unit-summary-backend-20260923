"""计量单位换算。

支持三种现场计量单位：袋(bag)、克(g)、公斤(kg)。换算一律走 Decimal，
最终公斤数按规定精度保留三位小数（精确到克），四舍五入 ROUND_HALF_UP，
绝不使用浮点猜测比例。
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

# 原始单位 -> 规范化
UNIT_ALIASES = {
    "bag": "bag", "bags": "bag", "dai": "bag", "袋": "bag",
    "g": "g", "gram": "g", "grams": "g", "ke": "g", "克": "g",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "gongjin": "kg", "公斤": "kg", "千克": "kg",
}
UNIT_BAG = "bag"
UNIT_GRAM = "g"
UNIT_KG = "kg"
SUPPORTED_UNITS = {UNIT_BAG, UNIT_GRAM, UNIT_KG}

KG_QUANT = Decimal("0.001")  # 公斤保留三位小数（克级）
PACKAGE_KG_QUANT = Decimal("0.0001")  # 袋规格本身可保留四位小数(如 20.0~40.0 常见,留余量)

REASON_MISSING_SPEC = "missing_spec"          # 无生效规格版本
REASON_MISSING_PACKAGE = "missing_package"    # 袋计量但袋规格为空
REASON_AMBIGUOUS_PRODUCT = "ambiguous_product"  # 同名/别名匹配到多个产品
REASON_UNSUPPORTED_UNIT = "unsupported_unit"


class ConversionError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def normalize_unit(unit):
    if unit is None:
        return None
    key = str(unit).strip().lower()
    return UNIT_ALIASES.get(key)


def to_decimal(value, field="数值"):
    if value is None:
        raise ConversionError(REASON_UNSUPPORTED_UNIT, f"{field}不能为空")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ConversionError(REASON_UNSUPPORTED_UNIT, f"{field}不是合法数字: {value!r}")
    if d < 0:
        raise ConversionError(REASON_UNSUPPORTED_UNIT, f"{field}不能为负: {value!r}")
    return d


def quantize_kg(value: Decimal) -> Decimal:
    return value.quantize(KG_QUANT, rounding=ROUND_HALF_UP)


def normalize_package_kg(value) -> Decimal:
    d = to_decimal(value, "袋规格")
    if d <= 0:
        raise ConversionError(REASON_MISSING_PACKAGE, "每袋公斤数必须大于0")
    return d.quantize(PACKAGE_KG_QUANT, rounding=ROUND_HALF_UP)


def convert_to_kg(quantity, unit, package_kg=None):
    """把原始数量按原始单位换算为公斤 Decimal（已量化到 0.001kg）。

    袋计量必须提供每袋公斤数（来自记录当时包装或产品规格版本）。
    """
    qty = to_decimal(quantity, "原始数量")
    u = normalize_unit(unit)
    if u is None or u not in SUPPORTED_UNITS:
        raise ConversionError(
            REASON_UNSUPPORTED_UNIT, f"不支持的计量单位: {unit!r}(仅支持 袋/克/公斤)"
        )

    if u == UNIT_KG:
        return quantize_kg(qty)
    if u == UNIT_GRAM:
        return quantize_kg(qty / Decimal("1000"))

    # 袋
    if package_kg is None:
        raise ConversionError(REASON_MISSING_PACKAGE, "袋计量缺少每袋公斤数，无法换算")
    pkg = to_decimal(package_kg, "每袋公斤数")
    if pkg <= 0:
        raise ConversionError(REASON_MISSING_PACKAGE, "每袋公斤数必须大于0")
    return quantize_kg(qty * pkg)


def conversion_input_hash(*, feed_type, quantity, unit, package_kg,
                          product_id, spec_version_id):
    """换算输入指纹：任一输入（含采用的规格版本）变化都会得到不同指纹，
    用于识别真正受影响的记录与幂等防重复换算。"""
    import hashlib
    parts = [
        str(feed_type or ""),
        str(quantity),
        str(normalize_unit(unit)),
        f"{to_decimal(package_kg):f}" if package_kg is not None else "",
        str(product_id) if product_id is not None else "",
        str(spec_version_id) if spec_version_id is not None else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
