"""产品识别与生效规格版本裁定。

现场人员把产品全名、简称和生产批号混写在同一字段，本模块负责：
1. 从原始文本中剥离生产批号，得到产品名称；
2. 用全名/简称/别名做**确定性**精确匹配（不做模糊猜测）；
3. 跟随"同名裁定合并"指向；
4. 按投喂日期选取生效期明确的规格版本。

匹配到多个产品即同名冲突，交人工复核，系统不替用户选择。
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from ..models import FeedProduct, FeedProductVersion
from .units import REASON_AMBIGUOUS_PRODUCT, REASON_MISSING_SPEC

# 括号内含数字/批号关键词的片段： (20240301) 【批号A01】 等
_PAREN = re.compile(r"[（(\[【][^）)\]】]*[0-9A-Za-z][^）)\]】]*[）)\]】]")
# 批号被剥离后残留的空括号： （ ）、() 等
_EMPTY_PAREN = re.compile(r"[（(\[【]\s*[）)\]】]")
# 显式批号标记： 批号:xxx / 生产批号 xxx / LOT xxx / BATCH xxx / NO.xxx / #xxx
_LOT_MARK = re.compile(
    r"(?:生产批号|批号|批\s*号|lot\.?|batch\.?|no\.?|#)\s*[:：]?\s*[0-9A-Za-z][0-9A-Za-z\-/]*",
    re.IGNORECASE,
)


def split_name_and_lot(raw_text):
    """返回 (产品名称, 生产批号或None)。无法可靠识别时批号可能为 None。"""
    if raw_text is None:
        return "", None
    text = str(raw_text).strip()
    lot = None

    m = _LOT_MARK.search(text)
    if m:
        lot = m.group(0)
        text = (text[:m.start()] + " " + text[m.end():])

    # 再剥离括号片段：优先把含"批/lot/batch"的视为批号
    for pm in _PAREN.finditer(text):
        frag = pm.group(0)
        if lot is None and re.search(r"批|lot|batch", frag, re.IGNORECASE):
            lot = frag
    text = _PAREN.sub(" ", text)
    text = _EMPTY_PAREN.sub(" ", text)

    name = re.sub(r"\s+", "", text).strip(" ，,、;；")
    return name, (lot.strip() if lot else None)


def _name_key(value):
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _alias_keys(product: FeedProduct):
    keys = {_name_key(product.full_name)}
    if product.short_name:
        keys.add(_name_key(product.short_name))
    if product.aliases:
        for a in re.split(r"[,，、;；]", product.aliases):
            if a.strip():
                keys.add(_name_key(a))
    keys.discard("")
    return keys


def _follow_merge(db: Session, product: FeedProduct) -> FeedProduct:
    seen = set()
    while product and product.status == "merged" and product.merged_into_id:
        if product.id in seen:
            break  # 合并环保护
        seen.add(product.id)
        product = db.query(FeedProduct).filter(FeedProduct.id == product.merged_into_id).first()
    return product


@dataclass
class Resolution:
    product: Optional[FeedProduct] = None
    version: Optional[FeedProductVersion] = None
    name: str = ""
    lot: Optional[str] = None
    status: str = "converted"  # converted / review
    reason: Optional[str] = None


def resolve(db: Session, raw_text, feeding_day: date,
            override_product_id: Optional[int] = None) -> Resolution:
    name, lot = split_name_and_lot(raw_text)
    result = Resolution(name=name, lot=lot)

    # 人工复核显式裁定的产品优先（不再走名称匹配，避免同名歧义复发）
    if override_product_id is not None:
        product = _follow_merge(
            db, db.query(FeedProduct).filter(FeedProduct.id == override_product_id).first()
        )
        if product is None:
            result.status = "review"
            result.reason = REASON_MISSING_SPEC
            return result
        result.product = product
        version = effective_version(db, product.id, feeding_day)
        if version is None:
            result.status = "review"
            result.reason = REASON_MISSING_SPEC
            return result
        result.version = version
        return result

    if not name:
        result.status = "review"
        result.reason = REASON_MISSING_SPEC
        return result

    key = _name_key(name)
    candidates = [p for p in db.query(FeedProduct).all() if key in _alias_keys(p)]
    # 同名冲突：同一名字对应多个（未合并到同一目标的）产品
    roots = {}
    for p in candidates:
        root = _follow_merge(db, p)
        if root is not None:
            roots[root.id] = root
    if len(roots) > 1:
        result.status = "review"
        result.reason = REASON_AMBIGUOUS_PRODUCT
        return result
    if not roots:
        result.status = "review"
        result.reason = REASON_MISSING_SPEC
        return result

    product = next(iter(roots.values()))
    result.product = product

    version = effective_version(db, product.id, feeding_day)
    if version is None:
        result.status = "review"
        result.reason = REASON_MISSING_SPEC
        return result
    result.version = version
    return result


def effective_version(db: Session, product_id: int, day: date) -> Optional[FeedProductVersion]:
    """返回投喂日生效的规格版本：effective_from <= day < effective_to。

    生效期重叠时取版本号更大的一版（新版本优先）。
    """
    versions = db.query(FeedProductVersion).filter(
        FeedProductVersion.product_id == product_id,
        FeedProductVersion.effective_from <= day,
    ).all()
    matching = [
        v for v in versions
        if v.effective_to is None or day < v.effective_to
    ]
    if not matching:
        return None
    return max(matching, key=lambda v: (v.version_number, v.id))
