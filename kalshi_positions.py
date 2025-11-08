import time
import os
import datetime
import base64
from urllib.parse import urlencode

import requests
import pandas as pd
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

# ---------- Load credentials ----------
load_dotenv()
ENV = os.getenv("ENV", "DEMO")
API_KEY_ID = os.getenv(f"{ENV}_KEYID")
PRIVATE_KEY_PATH = os.getenv(f"{ENV}_KEYFILE")
if ENV == "DEMO":
    BASE_URL = "https://demo-api.kalshi.co"  # change to prod if needed
else:
    BASE_URL = "https://api.elections.kalshi.com"

if not API_KEY_ID or not PRIVATE_KEY_PATH:
    raise RuntimeError(f"Missing {ENV}_KEYID or {ENV}_KEYFILE in .env")

with open(PRIVATE_KEY_PATH, "rb") as key_file:
    PRIVATE_KEY = serialization.load_pem_private_key(
        key_file.read(), password=None, backend=default_backend()
    )

# ---------- Core signing helpers ----------
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
    signature = create_signature(PRIVATE_KEY, timestamp, method, path)
    return {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
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


def kalshi_fee(price_dollars: float) -> float:
    """
    Compute Kalshi taker fee per contract (in dollars).
    Formula: 0.07 * P * (1 - P), rounded up to the nearest cent.
    No intermediate rounding; only final ceiling to cent.
    """
    return 0.07 * price_dollars * (1 - price_dollars)


def average_share_cost(row):
    """
    Calculate the true average cost per contract (including fees),
    normalized into YES terms.
    """
    fill_count = row.get("fill_count", 0)
    if not fill_count or fill_count == 0:
        return None

    fill_price = row["taker_fill_cost"] / row["fill_count"] / 100
    fee_per_contract = row["taker_fees"] / row["fill_count"] / 100
    action = row["action"].lower()
    side = row["side"].lower()

    # BUY YES → pay price + fees
    if action == "buy" and side == "yes":
        return fill_price + fee_per_contract

    # SELL YES → receive (1 - price) - fees
    elif action == "sell" and side == "yes":
        return (1 - fill_price) - fee_per_contract

    # BUY NO → same as SELL YES → receive (1 - price) - fees
    elif action == "buy" and side == "no":
        return (1 - fill_price) - fee_per_contract

    # SELL NO → same as BUY YES → pay price + fees
    elif action == "sell" and side == "no":
        return fill_price + fee_per_contract

    else:
        return None


def normalized_signed_shares(row):
    """Compute signed position in YES terms."""
    action = row["action"].lower()
    side = row["side"].lower()
    count = row["fill_count"]

    if side == "yes":
        return count if action == "buy" else -count
    elif side == "no":
        # buying NO == selling YES
        return -count if action == "buy" else count
    else:
        return 0


def track_position(group):
    """
    Tracks net YES-equivalent position, cost basis, and total fees.
    Handles partial closes and flips robustly.
    """
    net_pos = 0
    avg_cost = 0.0
    total_fees = group["taker_fees_dollars"].sum()

    for _, row in group.sort_values("created_time").iterrows():
        action = row["action"].lower()
        side = row["side"].lower()
        price = row["avg_share_cost_dollars"]
        count = row["fill_count"]

        abs_pos = abs(net_pos)
        if action == "buy" and side == "yes":
            net_pos += count
            avg_cost = (avg_cost * abs_pos + price * count) / abs_pos

    return pd.Series({
        "net_yes_position": net_pos,
        "avg_share_price": avg_cost,
        "total_fees": total_fees,
    })



def realize_now(row, df_summary):
    """
    Given a position and market summary, compute:
      - liquidation value using fee-adjusted prices
      - actual Kalshi fees (from summary)
    """
    mkt = df_summary[df_summary["market_ticker"] == row["ticker"]]
    if mkt.empty:
        return pd.Series({
            "current_net_value_dollars": 0.0,
            "current_net_value_per_share": 0.0,
            "fee_component_dollars": 0.0,
            "side_to_sell": None
        })

    mkt = mkt.iloc[0]
    net_pos = row["net_yes_position"]
    avg_cost = row["avg_share_price"]
    abs_pos = abs(net_pos)

    # Determine which side to sell
    if net_pos > 0:
        # Long YES → sell YES
        sell_price = mkt["yes_bid_effective"]
        fee = mkt["fee_yes_bid"]
        side = "YES"
    elif net_pos < 0:
        # Short YES → sell NO
        sell_price = mkt["no_bid_effective"]
        fee = mkt["fee_no_bid"]
        side = "NO"
    else:
        return pd.Series({
            "current_net_value_dollars": 0.0,
            "current_net_value_per_share": 0.0,
            "fee_component_dollars": 0.0,
            "side_to_sell": None
        })

    return pd.Series({
        "current_net_value_dollars": round(sell_price * abs_pos, 2),
        "current_net_value_per_share": round(sell_price * abs_pos / abs_pos if abs_pos != 0 else 0.00, 2),
        "fee_component_dollars": round(fee * abs_pos, 4),
        "side_to_sell": side
    })


def get_market_summary(event_ticker: str):
    """Fetch and summarize active Kalshi markets for a given event."""
    t0 = time.perf_counter()

    t0_0 = time.perf_counter()
    markets = kalshi_get("/trade-api/v2/markets", params={"event_ticker": event_ticker})
    print(f"Markets req time taken: {time.perf_counter() - t0_0:.3f} seconds")
    active_markets = [m for m in markets["markets"] if m["status"] == "active"]

    summary = []
    for m in active_markets:
        yes_bid = m["yes_bid"] / 100
        yes_ask = m["yes_ask"] / 100
        no_bid = m["no_bid"] / 100
        no_ask = m["no_ask"] / 100

        # Fees
        fee_yes_bid = kalshi_fee(yes_bid)
        fee_yes_ask = kalshi_fee(yes_ask)
        fee_no_bid = kalshi_fee(no_bid)
        fee_no_ask = kalshi_fee(no_ask)

        # Effective prices
        yes_bid_eff = yes_bid - fee_yes_bid
        yes_ask_eff = yes_ask + fee_yes_ask
        no_bid_eff = no_bid - fee_no_bid
        no_ask_eff = no_ask + fee_no_ask

        summary.append({
            "market_ticker": m["ticker"],
            "team": m["yes_sub_title"],
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": m["last_price"] / 100,
            "yes_bid_effective": yes_bid_eff,
            "yes_ask_effective": yes_ask_eff,
            "no_bid_effective": no_bid_eff,
            "no_ask_effective": no_ask_eff,
            "fee_yes_bid": fee_yes_bid,
            "fee_yes_ask": fee_yes_ask,
            "fee_no_bid": fee_no_bid,
            "fee_no_ask": fee_no_ask,
        })

    df_markets = pd.DataFrame(summary)

    t1 = time.perf_counter()
    print(f"✅ Market summary computed in {t1 - t0:.3f} seconds")
    return df_markets


def compute_positions(event_ticker: str, df_markets: pd.DataFrame):
    """
    Fetch pre-computed positions for a given event from Kalshi and
    return them in the same format as the old manual calculation.
    """
    t0 = time.perf_counter()

    # ---- Fetch positions ----
    t1 = time.perf_counter()
    data = kalshi_get("/trade-api/v2/portfolio/positions", params={"event_ticker": event_ticker})
    print(f"Positions req time taken: {time.perf_counter() - t1:.3f} seconds")

    market_positions = pd.DataFrame(data.get("market_positions", []))
    # print(market_positions.to_string())
    if market_positions.empty:
        print("⚠️ No active market positions found for this event.")
        return pd.DataFrame(columns=[
            "ticker", "net_yes_position", "avg_share_price",
            "fees_paid_dollars", "realized_pnl_dollars",
            "market_exposure_dollars", "liquidation_value"
        ])

    # ---- Convert & clean numeric fields ----
    for col in [
        "fees_paid_dollars",
        "market_exposure_dollars",
        "realized_pnl_dollars",
        "total_traded_dollars",
    ]:
        if col in market_positions.columns:
            market_positions[col] = market_positions[col].astype(float)

    # ---- Derive legacy-compatible columns ----
    market_positions["ticker"] = market_positions["ticker"].astype(str)
    market_positions["net_yes_position"] = market_positions["position"]          # rename
    market_positions["avg_share_price"] = (
        market_positions["market_exposure_dollars"] / market_positions["position"].abs()
    ).round(4)

    # Optional join with market info (for price context)
    merged = pd.merge(
        market_positions,
        df_markets[
            [
                "market_ticker",
                "yes_bid_effective",
                "yes_ask_effective",
                "no_bid_effective",
                "no_ask_effective",
            ]
        ],
        left_on="ticker",
        right_on="market_ticker",
        how="left",
    ).drop(columns=["market_ticker"], errors="ignore")

    # ---- Compute liquidation value ----
    def compute_liquidation(row):
        pos = row["net_yes_position"]
        if pos == 0:
            return 0.0

        # positive → long YES → sell at yes_bid_effective
        if pos > 0:
            price = row["yes_bid_effective"]
            return pos * price

        # negative → long NO (short YES) → sell at no_bid_effective
        else:
            price = row["no_bid_effective"]
            return abs(pos) * price

    merged["liquidation_value"] = merged.apply(compute_liquidation, axis=1).round(2)

    t2 = time.perf_counter()
    print(f"✅ Position tracking retrieved in {t2 - t0:.3f} seconds")

    # ---- Return same columns as before ----
    return merged[
        [
            "ticker",
            "net_yes_position",
            "avg_share_price",
            "fees_paid_dollars",
            "realized_pnl_dollars",
            "market_exposure_dollars",
            "liquidation_value",
        ]
    ]

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

if __name__ == "__main__":
    start_time = time.perf_counter()
    EVENT_TICKER = "KXNFLGAME-25OCT12CLEPIT"

    df_markets = get_market_summary(EVENT_TICKER)
    positions_with_liquidation = compute_positions(EVENT_TICKER, df_markets)

    print(positions_with_liquidation.to_string())

    end_time = time.perf_counter()
    print(f"\n--- Total execution time: {end_time - start_time:.3f} seconds ---")