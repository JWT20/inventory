"""Single source of truth for customer pricing.

Both the order flow (order form preview, confirmed order lines, weekly summary)
and the customer assortment endpoint must resolve "what does this customer pay
for this product?" the same way. Keeping the waterfall in one place prevents the
two call paths from drifting apart again.
"""


def calc_effective_price(
    default_price: float | None,
    unit_price: float | None,
    discount_type: str | None,
    discount_value: float | None,
    customer_discount_pct: float | None,
) -> float | None:
    """Price waterfall: fixed customer price > sku discount > customer discount > default.

    1. A fixed per-customer unit price for this product wins outright.
    2. Otherwise a product-specific discount (percentage/fixed) for this customer.
    3. Otherwise the customer's general discount percentage.
    4. Otherwise the catalogue default price.
    """
    if unit_price is not None:
        return unit_price
    if default_price is None:
        return None
    if discount_type and discount_value is not None:
        if discount_type == "percentage":
            return round(default_price * (1 - discount_value / 100), 2)
        if discount_type == "fixed":
            return round(max(default_price - discount_value, 0), 2)
    if customer_discount_pct is not None:
        return round(default_price * (1 - customer_discount_pct / 100), 2)
    return default_price
