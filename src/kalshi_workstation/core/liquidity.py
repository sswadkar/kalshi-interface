"""Estimated Liquidity Incentive Program rewards for resting bids.

The model, per side of a market's book (YES bids and NO bids are scored separately):

1. Gate: if the side's total resting size is below the program's Target Size, the
   side pays nothing to anyone.
2. Anchor: walk down from the best bid accumulating size. Every level until the
   cumulative size reaches 200 contracts has full weight (1.0).
3. Decay: each cent below that anchor multiplies the weight by the program's
   discount factor (typically 0.5).
4. Cutoff: only the top Target Size contracts count; the level that crosses the
   cutoff counts in full, deeper levels count zero.
5. Share: an order earns weight x size / (total weighted size on its side) of that
   side's pool, assumed to be half of the market's daily reward.

This reproduces worked examples but is not an official specification; treat every
result as an estimate that assumes the current book persists.
"""
from kalshi_workstation.core.prices import dollars_to_cents


DISCOUNT_ANCHOR_SIZE = 200   # FLAT: cumulative depth at which the geometric decay begins


SIDE_POOL_FRACTION = 0.5     # each side draws ~50% of the market's daily reward pool


DEFAULT_DISCOUNT = 0.5       # per-cent decay base when the program doesn't specify one


def _cents(p):
    return int(round(float(p) * 100))


def level_weight(price_cents, anchor_cents, df):
    """Reward weight for a resting level: 1.0 at/above the 200-anchor, else df^(cents below)."""
    if anchor_cents is None or price_cents >= anchor_cents:
        return 1.0
    return df ** (anchor_cents - price_cents)


def qualify_side(levels, target_size, discount_factor):
    """Score one bid book side under the 200-anchor / geometric-decay / target-cutoff model.

    levels: [[price_str, size_str], ...] (a bid book: yes_dollars or no_dollars).
    Returns dict:
      reached      – gate: cumulative depth reached target_size (side pays $0 if False)
      ref_cents    – best (top-of-book) price, for placement/display
      anchor_cents – price of the level crossing cumulative-200 (where decay begins)
      cutoff_cents – lowest price still within the counted (top target_size) band
      score        – Σ weight*size over counted levels (weight per level_weight())
      total_size   – cumulative size over counted levels
      n_levels, df
    """
    df = discount_factor if (discount_factor and discount_factor > 0) else DEFAULT_DISCOUNT
    lv = sorted(([_cents(p), float(s)] for p, s in levels), key=lambda x: -x[0])
    if not lv:
        return {"reached": False, "ref_cents": None, "anchor_cents": None, "cutoff_cents": None,
                "total_size": 0.0, "score": 0.0, "n_levels": 0, "df": df}
    ref = lv[0][0]

    # Pass 1: locate the discount anchor — the level crossing cumulative-200 (FLAT).
    # If the side never reaches 200, the whole (small) book is inside the top band -> no decay.
    cum, anchor_cents = 0.0, lv[-1][0]
    for cents, size in lv:
        cum += size
        if cum >= DISCOUNT_ANCHOR_SIZE:
            anchor_cents = cents
            break

    # Pass 2: accumulate weighted score until cumulative hits target_size (level-inclusive).
    total, score, used = 0.0, 0.0, 0
    for cents, size in lv:
        score += level_weight(cents, anchor_cents, df) * size
        total += size
        used += 1
        if total >= target_size:
            return {"reached": True, "ref_cents": ref, "anchor_cents": anchor_cents,
                    "cutoff_cents": cents, "total_size": total, "score": score,
                    "n_levels": used, "df": df}
    return {"reached": False, "ref_cents": ref, "anchor_cents": anchor_cents,
            "cutoff_cents": lv[-1][0], "total_size": total, "score": score,
            "n_levels": used, "df": df}


def estimated_daily_reward(action, side, order, rem, prog, ob):
    """Estimate $/day an order earns from the Liquidity Incentive Program.

    Uses the model in this module's docstring: weight = 1.0 at/above the 200-contract
    anchor, else discount_factor^(anchor_cents - price_cents); share = order_score /
    side_score; zero if the side misses Target Size or the order is below the cutoff.
    """
    ts, df = prog["target_size"], prog["discount_factor"]
    reward_side_yes = (action == "buy" and side == "yes") or (action == "sell" and side == "no")
    ob_side = ob.get("yes_dollars") if reward_side_yes else ob.get("no_dollars")
    q = qualify_side(ob_side or [], ts, df)
    if not q["reached"]:                              # this side's Target-Size gate not met -> $0
        return 0.0
    rew_px = dollars_to_cents(order.get("yes_price_dollars") if reward_side_yes else order.get("no_price_dollars"))
    if q["score"] <= 0 or rew_px <= 0:
        return 0.0
    cutoff = q.get("cutoff_cents")
    if cutoff is not None and rew_px < cutoff:        # deeper than the top Target-Size band -> $0
        return 0.0
    order_score = level_weight(rew_px, q["anchor_cents"], q["df"]) * rem
    share = order_score / q["score"]                  # q["score"] already includes this order
    return prog["daily_dollars"] * share * SIDE_POOL_FRACTION
