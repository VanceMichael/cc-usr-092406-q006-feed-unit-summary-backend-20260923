"""周期分析、追溯与差额核对共用的逐笔序列化。

每一笔投喂都要说明：原始数量/原始单位/当时包装、裁定的产品、
实际采用的规格版本（版本号、生效期、袋规格）与换算后的公斤数。
"""
from ..models import FeedingRecord


def feeding_line(record: FeedingRecord) -> dict:
    version = record.spec_version
    product = record.product
    return {
        "record_id": record.id,
        "batch_id": record.batch_id,
        "feeding_date": record.feeding_date,
        "feed_type": record.feed_type,
        "original_quantity": record.quantity,
        "original_unit": record.unit,
        "record_package_kg": record.package_kg,
        "package_batch_no": record.package_batch_no,
        "product_id": product.id if product else record.product_id,
        "product_name": product.full_name if product else None,
        "spec_version_id": version.id if version else record.spec_version_id,
        "spec_version_number": version.version_number if version else None,
        "spec_package_kg": record.resolved_package_kg,
        "effective_from": version.effective_from if version else None,
        "effective_to": version.effective_to if version else None,
        "quantity_kg": record.feed_quantity,
        "conversion_status": record.conversion_status,
        "review_reason": record.review_reason,
        "signed": record.signed_at is not None,
    }
