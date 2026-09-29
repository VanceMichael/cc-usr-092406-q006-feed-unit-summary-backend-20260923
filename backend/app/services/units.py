"""单位与精度换算：袋、克、公斤。

精度规定（只能用登记在案的包装规格换算，绝不猜测比例）：
- 公斤(kg)：保留 3 位小数（克级精度）
- 克(g)  ：整数/至多 3 位小数输入，换算公斤除以 1000，保留 6 位小数后 round 到 3
- 袋(bag)：数量本身可带 0.5 等分数袋，乘以规格的每袋净重；保留 3 位小数
- 最终汇总公斤统一保留 3 位小数

对外返回的所有公斤值均为 round(x, 3)。
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

KG_PRECISION = Decimal("0.001")          # 公斤保留千分之一公斤 = 1 克
GRAMS_PER_KG = Decimal("1000")

# 允许的原始单位（登记/解析结果规范化后的取值）
UNIT_BAG = "bag"
UNIT_GRAM = "g"
UNIT_KG = "kg"
VALID_UNITS = (UNIT_BAG, UNIT_GRAM, UNIT_KG)

# 现场可能写的各种写法 -> 规范单位
UNIT_ALIASES = {
    "bag": UNIT_BAG, "bags": UNIT_BAG, "袋": UNIT_BAG, "包": UNIT_BAG,
    "g": UNIT_GRAM, "gram": UNIT_GRAM, "grams": UNIT_GRAM, "克": UNIT_GRAM,
    "kg": UNIT_KG, "kgs": UNIT_KG, "kilogram": UNIT_KG, "kilograms": UNIT_KG,
    "公斤": UNIT_KG, "千克": UNIT_KG,
}


def normalize_unit(raw_unit) -> Optional[str]:
    """把现场单位写法规范化为 bag/g/kg；无法识别返回 None。"""
    if raw_unit is None:
        return None
    key = str(raw_unit).strip().lower()
    return UNIT_ALIASES.get(key)


def _q(value) -> Decimal:
    return Decimal(str(value)).quantize(KG_PRECISION, rounding=ROUND_HALF_UP)


def to_kilograms(raw_quantity, raw_unit: str, kg_per_bag: Optional[float] = None) -> float:
    """
    按规定精度把原始数量换算成公斤。
    单位为袋时必须提供生效规格的 kg_per_bag；缺失抛 SpecRequiredError，由调用方进复核。
    """
    if raw_quantity is None:
        raise ValueError("原始数量缺失")
    qty = Decimal(str(raw_quantity))
    if qty < 0:
        raise ValueError("原始数量不能为负")

    if raw_unit == UNIT_KG:
        return float(_q(qty))
    if raw_unit == UNIT_GRAM:
        return float(_q(qty / GRAMS_PER_KG))
    if raw_unit == UNIT_BAG:
        if kg_per_bag is None:
            raise SpecRequiredError("袋数换算缺少每袋净重规格")
        bag = Decimal(str(kg_per_bag))
        if bag <= 0:
            raise ValueError("每袋净重必须为正数")
        return float(_q(qty * bag))
    raise InvalidUnitError(f"不支持的单位: {raw_unit}")


class InvalidUnitError(ValueError):
    """现场单位无法识别。"""


class SpecRequiredError(ValueError):
    """以袋计量但缺少生效包装规格。"""


def round_kg(value) -> float:
    """汇总口径的公斤四舍五入（财务/报表统一精度）。"""
    if value is None:
        return 0.0
    return float(Decimal(str(value)).quantize(KG_PRECISION, rounding=ROUND_HALF_UP))
