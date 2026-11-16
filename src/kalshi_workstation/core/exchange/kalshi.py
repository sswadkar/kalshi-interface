"""Kalshi HTTP/signing adapter. Private keys are loaded on first authenticated use."""
import base64
import datetime
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from kalshi_workstation.config import ENV, PROJECT_ROOT

BASE_URL = "https://demo-api.kalshi.co" if ENV == "DEMO" else "https://api.elections.kalshi.com"
ORDER_V2_ENDPOINT = "/trade-api/v2/portfolio/events/orders"

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
    """Build signed headers for GET/POST requests."""
    timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
    api_key_id, private_key = get_credentials()
    signature = create_signature(private_key, timestamp, method, path)
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }


def kalshi_get(path: str, params: dict | None = None):
    """Authenticated GET request with optional query parameters."""
    query = f"?{urlencode(params)}" if params else ""
    full_path = f"{path}{query}"

    headers = kalshi_headers("GET", path if not params else full_path.split("?")[0])
    resp = requests.get(BASE_URL + full_path, headers=headers)
    resp.raise_for_status()
    return resp.json()


def kalshi_delete(path: str, params: dict | None = None):
    """Authenticated GET request with optional query parameters."""
    query = f"?{urlencode(params)}" if params else ""
    full_path = f"{path}{query}"

    headers = kalshi_headers("DELETE", path if not params else full_path.split("?")[0])
    resp = requests.delete(BASE_URL + full_path, headers=headers)
    resp.raise_for_status()
    return resp.json()


def kalshi_post(path: str, data: dict):
    """Authenticated POST request."""
    headers = kalshi_headers("POST", path)
    resp = requests.post(BASE_URL + path, headers=headers, json=data)
    if not resp.ok:
        print(f"❌ POST {path} failed: {resp.status_code} {resp.text}")
    return resp.json()


def get_user_info():
    return kalshi_get("/trade-api/v2/portfolio/balance")


def get_queue_positions(event_ticker: str):
    queue_data = kalshi_get("/trade-api/v2/portfolio/orders/queue_positions", params={"event_ticker": event_ticker})
    queue_positions = queue_data.get("queue_positions", [])
    if queue_positions is None:
        return []

    resting = []

    for q in queue_positions:
        order_id = q.get("order_id")
        if not order_id:
            continue

        order_data = kalshi_get(f"/trade-api/v2/portfolio/orders/{order_id}", params={"event_ticker": event_ticker})
        order = order_data.get("order", {})

        # Only track active (resting) orders
        if order.get("status") == "resting":
            resting.append({
                "order_id": order_id,
                "ticker": order.get("ticker"),
                "side": order.get("side", "").upper(),
                "action": order.get("action", "").upper(),
                "type": order.get("type", "limit").upper(),
                "price": order.get("yes_price_dollars") or order.get("no_price_dollars"),
                "queue_position": q.get("queue_position"),
                "remaining": order.get("remaining_count"),
                "created": order.get("created_time"),
                "last_update": order.get("last_update_time")
            })

    return resting
