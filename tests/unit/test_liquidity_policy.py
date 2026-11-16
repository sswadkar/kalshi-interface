from kalshi_workstation.core.market_data import MarketBook
from kalshi_workstation.core.liquidity import qualify_side
from kalshi_workstation.features.liquidity_provider.policy import desired_quotes


def test_book_snapshot_delta_and_opposite_side_price():
    book = MarketBook("SYNTHETIC")
    book.apply_snapshot({"yes_dollars_fp": [["0.4000", "200"]],
                         "no_dollars_fp": [["0.5500", "200"]], "ts_ms": 100})
    assert book.best_yes_bid() == 40
    assert book.best_yes_ask() == 45
    book.apply_delta({"side": "yes", "price_dollars": "0.4000", "delta_fp": "-200"})
    assert book.best_yes_bid() is None


def test_quote_policy_uses_supplied_inventory_and_qualification():
    book = MarketBook("SYNTHETIC")
    book.apply_snapshot({"yes_dollars_fp": [["0.4000", "200"]],
                         "no_dollars_fp": [["0.5500", "200"]]})
    market = {"ticker": "SYNTHETIC", "flatten_band": 300}
    program = {"target_size": 200, "discount_factor": .5}
    assert desired_quotes(market, program, book, {"risk_level": "conservative"}, {}) == {
        "yes": 39, "no": 54}
    assert desired_quotes(market, program, book, {"risk_level": "conservative"},
                          {"SYNTHETIC": 300}) == {"yes": None, "no": 54}
    book.ready = False
    assert desired_quotes(market, program, book, {"risk_level": "conservative"}, {}) == {
        "yes": None, "no": None}


def test_existing_reward_gate_and_discount_anchor():
    # Characterizes the existing model; does not certify the exchange's reward rules.
    levels = [["0.4000", "200"], ["0.3900", "100"]]
    result = qualify_side(levels, 300, .5)
    assert result["reached"]
    assert result["score"] == 250
    assert result["cutoff_cents"] == 39
    assert not qualify_side(levels, 400, .5)["reached"]
