"""The only code that creates or cancels orders on the exchange.

Callers own their dry-run decisions and interpret the responses. There is no
durable order ledger: after a restart, the exchange is the record of truth.
"""
from kalshi_workstation.core.exchange import kalshi

def submit_order(payload: dict):
    """Submit one already constructed order; preserve the exchange response."""
    return kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, payload)

def cancel_order(order_id: str):
    """Request cancellation; callers must interpret the response or exception."""
    return kalshi.kalshi_delete(f"{kalshi.ORDER_V2_ENDPOINT}/{order_id}")
