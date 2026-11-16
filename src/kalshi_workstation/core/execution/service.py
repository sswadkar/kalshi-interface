"""Single entry point for runtime exchange mutations during the migration.

This first extraction preserves existing behavior. Callers still own their
current dry-run decisions and local order state. Central admission, durable
reservations, and uncertain-outcome reconciliation are subsequent work.
"""
from kalshi_workstation.core.exchange import kalshi

def submit_order(payload: dict):
    """Submit one already constructed order; preserve the exchange response."""
    return kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, payload)

def cancel_order(order_id: str):
    """Request cancellation; callers must interpret the response or exception."""
    return kalshi.kalshi_delete(f"{kalshi.ORDER_V2_ENDPOINT}/{order_id}")
