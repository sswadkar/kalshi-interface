# Kalshi Position Tracker & Trading API

A lightweight FastAPI service and CLI toolkit for interacting with [Kalshi](https://kalshi.com)’s trading API.  
It supports market polling, position tracking, and order execution using Kalshi’s PSS-signed authentication flow.

---

## Features

- **Secure Authentication** – Uses RSA-PSS signatures (via private keys) to sign all Kalshi API requests.  
- **Market Monitoring** – Continuously polls market data and open positions every few seconds.  
- **Trading Endpoints** – Provides `/api/order/buy`, `/api/order/sell`, and `/api/orders/cancel/{id}` endpoints for order execution.  
- **Position Tracking** – Computes liquidation value, realized PnL, and exposure per market.  
- **Environment Isolation** – Separate key handling for **demo** and **production** environments.  
- **Docker-Ready** – Containerized setup for quick deployment.  

---

## Project Structure

```
├── api_keys/
│ ├── demo/
│ │ └── private_key.pem
│ └── prod/
│   └── private_key.pem
├── static/
│ └── index.html # Basic frontend or status page
├── kalshi_positions.py # Core API & position logic
├── server.py # FastAPI web server
├── requirements.txt # Python dependencies
├── Dockerfile # Container build definition
├── README.md
└── LICENSE
```
---

## Environment Setup

Create a `.env` file in the project root:

```bash
# Select environment: DEMO or PROD
ENV=DEMO

# API credentials
DEMO_KEYID=<your_demo_key_id>
DEMO_KEYFILE=api_keys/demo/private_key.pem

# For production
PROD_KEYID=<your_prod_key_id>
PROD_KEYFILE=api_keys/prod/private_key.pem

# Event ticker to monitor
EVENT_TICKER=KXNFLGAME-25OCT12CLEPIT
```

### Install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
---

#  Running the Server
```bash
python server.py
```
The API runs by default at http://localhost:8000.

### Using Docker
```bash
docker build -t kalshi-api .
docker run -p 8000:8000 --env-file .env kalshi-api
```


# Polling Behavior
The server continuously runs two background tasks:

`poll_markets()` → updates market/position info every 0.5s

`poll_resting_orders()` → refreshes resting orders every 3s

These tasks maintain a real-time in-memory state that feeds the /api/status endpoint.

# Core Logic (kalshi_positions.py)

### Authentication:
Uses cryptographic signatures with cryptography to authorize every request.

### Fee Model:
Implements Kalshi’s taker fee formula

`fee=0.07×P×(1−P)`

where P is the contract price in dollars.

## Position Computation:
Calculates liquidation value, realized PnL, and net exposure across all active markets.

### Example API Call
```bash
curl -X POST http://localhost:8000/api/order/buy \
     -H "Content-Type: application/json" \
     -d '{"ticker": "KXNFLGAME-25OCT12CLEPIT", "side": "yes", "quantity": 2}'
```

### Example Response
```json
{
  "order": {
    "ticker": "KXNFLGAME-25OCT12CLEPIT",
    "status": "executed",
    "taker_fill_cost_dollars": "0.42",
    "taker_fees_dollars": "0.01",
    "last_update_time": "2025-11-07T00:15:23.125Z"
  }
}
```

# Developer Notes

- Designed for low-latency polling (≤ 500 ms loop).
- Easily extendable for event streaming or WebSocket output.
- static/index.html can be replaced with a richer UI dashboard.

# License
This project is distributed under the MIT License.
