"""HTTP adapter: rate limits, timeouts, 429 handling, and error responses."""
import time

import pytest
import requests

from kalshi_workstation.core.exchange import kalshi


class FakeResponse:
    def __init__(self, status, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else ("" if body is None else str(body))
        self.reason = "synthetic"
        self.ok = 200 <= status < 300

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


@pytest.fixture
def exchange(monkeypatch):
    """Unsigned, unthrottled requests against a scripted session."""
    class Free:
        def try_acquire(self, name):
            return True
    monkeypatch.setattr(kalshi, "kalshi_headers", lambda method, path: {})
    monkeypatch.setattr(kalshi, "LIMITERS", {name: Free() for name in kalshi.LIMITERS})
    sleeps = []
    monkeypatch.setattr(kalshi, "_sleep", sleeps.append)

    def install(*responses):
        session = FakeSession(*responses)
        monkeypatch.setattr(kalshi, "_session", session)
        session.sleeps = sleeps
        return session
    return install


def test_every_request_has_a_timeout(exchange):
    session = exchange(FakeResponse(200, {}), FakeResponse(200, {}), FakeResponse(200, {}))
    kalshi.kalshi_get("/trade-api/v2/markets", {"tickers": "A"})
    kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, {"ticker": "A"})
    kalshi.kalshi_delete(f"{kalshi.ORDER_V2_ENDPOINT}/1")
    assert all(call[2]["timeout"] == kalshi.TIMEOUT for call in session.calls)
    assert session.calls[0][1].endswith("/trade-api/v2/markets?tickers=A")


def test_reads_retry_after_rate_limiting_with_backoff(exchange):
    session = exchange(FakeResponse(429, {}), FakeResponse(429, {}), FakeResponse(200, {"ok": 1}))
    assert kalshi.kalshi_get("/trade-api/v2/portfolio/balance") == {"ok": 1}
    assert len(session.calls) == 3
    assert session.sleeps == [0.25, 0.5]


def test_reads_give_up_after_the_retry_budget(exchange):
    exchange(*[FakeResponse(429, {})] * (kalshi.MAX_429_RETRIES + 1))
    with pytest.raises(requests.HTTPError):
        kalshi.kalshi_get("/trade-api/v2/markets")


def test_order_creation_is_never_retried(exchange):
    session = exchange(FakeResponse(429, {"error": {"code": "too_many_requests", "message": "slow down"}}))
    result = kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, {"ticker": "A"})
    assert len(session.calls) == 1
    assert result["error"]["code"] == "too_many_requests"


def test_order_gateway_error_is_reported_as_uncertain_not_a_crash(exchange):
    exchange(FakeResponse(502, None, text="<html>Bad gateway</html>"))
    result = kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, {"ticker": "A"})
    assert result["error"]["code"] == "http_502"
    assert "may or may not" in result["error"]["message"]


def test_order_rejection_without_json_body_is_an_error(exchange):
    exchange(FakeResponse(400, None, text="bad request"))
    assert kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, {})["error"]["message"] == "bad request"


@pytest.mark.parametrize("method,path,bucket", [
    ("GET", kalshi.BALANCE_PATH, "balance"),
    ("GET", "/trade-api/v2/markets", "read"),
    ("GET", "/trade-api/v2/portfolio/orders", "read"),
    ("POST", kalshi.ORDER_V2_ENDPOINT, "write"),
    ("DELETE", f"{kalshi.ORDER_V2_ENDPOINT}/1", "write"),
])
def test_requests_draw_from_the_matching_bucket(method, path, bucket):
    assert kalshi._bucket(method, path) == bucket


def test_each_bucket_admits_its_budget_and_no_more_within_a_second():
    limiters = kalshi.build_limiters()

    def admitted(name, attempts):
        return sum(limiters[name].try_acquire("test", blocking=False) for _ in range(attempts))

    assert admitted("balance", 3) == 1                                  # no burst
    assert admitted("read", 30) == int(kalshi.READS_PER_SECOND)        # 15 by default
    assert admitted("write", 30) == int(kalshi.WRITES_PER_SECOND)      # 8 by default


def test_a_full_bucket_blocks_until_the_window_passes():
    limiter = kalshi.per_second(1)
    limiter.try_acquire("test")
    started = time.monotonic()
    limiter.try_acquire("test")
    assert 0.8 < time.monotonic() - started < 2.0


def test_resting_orders_use_two_requests_and_cents(monkeypatch):
    calls = []

    def get(path, params=None):
        calls.append(path)
        if path.endswith("queue_positions"):
            return {"queue_positions": [{"order_id": "n1", "queue_position_fp": "12.00"}]}
        return {"orders": [
            {"order_id": "n1", "ticker": "T", "side": "no", "action": "buy",
             "yes_price_dollars": "0.3500", "no_price_dollars": "0.6500", "remaining_count_fp": "5.00"},
            {"order_id": "y1", "ticker": "T", "side": "yes", "action": "sell",
             "yes_price_dollars": "0.4100", "no_price_dollars": "0.5900", "remaining_count_fp": "2.00"},
        ], "cursor": ""}

    monkeypatch.setattr(kalshi, "kalshi_get", get)
    orders = kalshi.get_resting_orders("EVENT")
    assert len(calls) == 2
    assert [(o["side"], o["price"], o["queue_position"], o["remaining"]) for o in orders] == [
        ("NO", 65, 12.0, 5.0), ("YES", 41, None, 2.0)]


def test_every_request_waits_on_its_bucket_before_sending(exchange, monkeypatch):
    session = exchange(FakeResponse(200, {}), FakeResponse(200, {}), FakeResponse(200, {}))
    drawn = []

    class Recording:
        def __init__(self, name):
            self.name = name

        def try_acquire(self, key):
            drawn.append((self.name, len(session.calls)))   # calls made before this draw
            return True

    monkeypatch.setattr(kalshi, "LIMITERS", {name: Recording(name) for name in kalshi.LIMITERS})
    kalshi.get_balance()
    kalshi.kalshi_post(kalshi.ORDER_V2_ENDPOINT, {"ticker": "A"})
    kalshi.kalshi_get("/trade-api/v2/markets")
    assert drawn == [("balance", 0), ("write", 1), ("read", 2)]
