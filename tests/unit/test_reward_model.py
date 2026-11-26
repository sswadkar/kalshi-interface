from kalshi_workstation.core.liquidity import qualify_side


def test_reward_gate_anchor_and_cutoff():
    # Characterizes the documented model; does not certify the exchange's reward rules.
    levels = [["0.4000", "200"], ["0.3900", "100"]]
    result = qualify_side(levels, 300, .5)
    assert result["reached"]
    assert result["score"] == 250       # 200 at full weight + 100 one cent below at 0.5
    assert result["cutoff_cents"] == 39
    assert not qualify_side(levels, 400, .5)["reached"]


def test_worked_example_share():
    # 150 @ 70c and 1000 @ 50c with target 1000: the 50c level crosses the cutoff
    # and counts in full; the anchor is at 50c (cumulative crosses 200 there).
    levels = [["0.7000", "150"], ["0.5000", "1000"]]
    result = qualify_side(levels, 1000, .5)
    assert result["anchor_cents"] == 50
    assert result["score"] == 1150
