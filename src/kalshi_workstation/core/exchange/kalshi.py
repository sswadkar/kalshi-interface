"""Kalshi HTTP/signing adapter. Private keys are loaded on first authenticated use.

Every request goes through one pooled session, a client-side rate limiter, and a
timeout. Reads and cancellations are retried after HTTP 429 (the exchange did not
process them). Order creation is never retried here: callers own that decision.
"""
import base64
import datetime
import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from kalshi_workstation.config import (
    BALANCE_PER_SECOND, ENV, PROJECT_ROOT, READS_PER_SECOND, WRITES_PER_SECOND,
)
from kalshi_workstation.core.prices import dollars_to_cents

logger = logging.getLogger(__name__)

BASE_URL = "https://demo-api.kalshi.co" if ENV == "DEMO" else "https://api.elections.kalshi.com"
ORDER_V2_ENDPOINT = "/trade-api/v2/portfolio/events/orders"
BALANCE_PATH = "/trade-api/v2/portfolio/balance"
# (connect, read) seconds. A read timeout on order creation leaves the outcome unknown.
TIMEOUT = (3.05, 10)
MAX_429_RETRIES = 3


class RateLimiter:
    """Thread-safe token bucket. `acquire` blocks until one request may be sent."""

    def __init__(self, rate: float, capacity: float | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(rate, 1.0)
        self._tokens = self.capacity
        self._clock, self._sleep = clock, sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
            self._sleep(wait)


LIMITERS = {
    "read": RateLimiter(READS_PER_SECOND),
    "write": RateLimiter(WRITES_PER_SECOND),
    # The exchange's balance bucket holds one second of budget: no burst.
    "balance": RateLimiter(BALANCE_PER_SECOND, capacity=1),
}


def _bucket(method: str, path: str) -> str:
    if path == BALANCE_PATH:
        return "balance"
    if method != "GET" and "/portfolio/" in path:
        return "write"
    return "read"


def _new_session():
    session = requests.Session()
    session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=32))
    return session


_session = _new_session()
_sleep = time.sleep


@lru_cache(maxsize=1)
def get_credentials():
    api_key_id = os.getenv(f"{ENV}_KEYID")
    key_path = os.getenv(f"{ENV}_KEYFILE")
    if not api_key_id or not key_path:
        raise RuntimeError(f"Missing {ENV}_KEYID or {ENV}_KEYFILE in configuration")
    path = Path(key_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with path.open("rb") as key_file:
        key = serialization.load_pem_private_key(
            key_file.read(), password=None, backend=default_backend())
    return api_key_id, key


def create_signature(private_key, timestamp, method, path):
    """Create Kalshi-compliant PSS signature."""
    message = f"{timestamp}{method}{path}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def kalshi_headers(method: str, path: str):
    """Build signed headers. `path` excludes the query string."""
    timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
    api_key_id, private_key = get_credentials()
    signature = create_signature(private_key, timestamp, method, path)
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }


def _send(method: str, path: str, params: dict | None = None, data: dict | None = None,
          retry_429: bool = True) -> requests.Response:
    query = f"?{urlencode(params)}" if params else ""
    attempts = MAX_429_RETRIES + 1 if retry_429 else 1
    for attempt in range(attempts):
        LIMITERS[_bucket(method, path)].acquire()
        response = _session.request(
            method, BASE_URL + path + query, headers=kalshi_headers(method, path),
            json=data, timeout=TIMEOUT)
        if response.status_code != 429 or attempt == attempts - 1:
            return response
        delay = 0.25 * 2 ** attempt
        logger.warning("Rate limited on %s %s; retrying in %.2fs", method, path, delay)
        _sleep(delay)
    raise AssertionError("unreachable")


def kalshi_get(path: str, params: dict | None = None):
    """Authenticated GET. Raises requests.HTTPError for non-2xx responses."""
    response = _send("GET", path, params)
    response.raise_for_status()
    return response.json()


def kalshi_delete(path: str, params: dict | None = None):
    """Authenticated DELETE. Raises requests.HTTPError for non-2xx responses."""
    response = _send("DELETE", path, params)
    response.raise_for_status()
    return response.json()


def kalshi_post(path: str, data: dict):
    """Authenticated POST, sent once. Non-2xx responses return {"error": {...}}.

    A 5xx response does not prove the exchange rejected the request, so its
    error message says the outcome is unknown.
    """
    response = _send("POST", path, data=data, retry_429=False)
    if response.ok:
        return response.json()
    logger.error("POST %s failed: %s %s", path, response.status_code, response.text[:500])
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 500:
        return {"error": {
            "code": f"http_{response.status_code}",
            "message": (f"Exchange returned HTTP {response.status_code}; the request may or may "
                        "not have been accepted. Check orders and positions."),
        }}
    if isinstance(body, dict) and "error" in body:
        return body
    return {"error": {"code": f"http_{response.status_code}",
                      "message": response.text[:200] or response.reason}}


def get_balance():
    """Raw balance response (integer cents). Uses the exchange's 1-per-second bucket."""
    return kalshi_get(BALANCE_PATH)


def _paged(path: str, key: str, params: dict):
    items, cursor = [], None
    while True:
        page = kalshi_get(path, params={**params, **({"cursor": cursor} if cursor else {})})
        batch = page.get(key) or []
        items += batch
        cursor = page.get("cursor") or page.get("next_cursor")
        if not cursor or not batch:
            return items


def get_resting_orders(event_ticker: str):
    """Resting orders for one event with queue positions: two requests, not one per order."""
    orders = _paged("/trade-api/v2/portfolio/orders", "orders",
                    {"event_ticker": event_ticker, "status": "resting", "limit": 200})
    queue = kalshi_get("/trade-api/v2/portfolio/orders/queue_positions",
                       params={"event_ticker": event_ticker}).get("queue_positions") or []
    positions = {q.get("order_id"): q.get("queue_position_fp", q.get("queue_position"))
                 for q in queue}

    resting = []
    for order in orders:
        side = (order.get("side") or "").lower()
        price = order.get("yes_price_dollars") if side == "yes" else order.get("no_price_dollars")
        remaining = order.get("remaining_count_fp", order.get("remaining_count"))
        queue_position = positions.get(order.get("order_id"))
        resting.append({
            "order_id": order.get("order_id"),
            "ticker": order.get("ticker"),
            "side": side.upper(),
            "action": (order.get("action") or "").upper(),
            "type": (order.get("type") or "limit").upper(),
            "price": dollars_to_cents(price, default=None),
            "queue_position": _to_number(queue_position),
            "remaining": _to_number(remaining),
            "created": order.get("created_time"),
            "last_update": order.get("last_update_time"),
        })
    return resting


def _to_number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

