# Arbitrage

[← Back to the README](../README.md)

This page covers how the arbitrage page finds pairs, why a person has to approve a group before it can trade automatically, and how the two orders in a pair are sent.

## The basic trade

A Kalshi contract pays $1 if its question resolves YES and $0 otherwise. Suppose two markets ask the same question. YES on market A is offered at 46¢ and NO on market B at 49¢. Buy both for 95¢ and exactly one of them pays $1, whatever happens. That's 5¢ per pair before fees.

Takers pay about `0.07 × P × (1 − P)` per contract (P in dollars), so roughly 1.75¢ on each of these legs and 3.5¢ for the pair. The minimum edge you set has to clear the fees and the per-leg price buffer described below. The edges the page displays are gross.

## Groups

The app doesn't search Kalshi for pairs. You build **groups** yourself: each one is a set of markets you believe ask the same question, often listed under different events. The app re-quotes every grouped market twice a second, using one batched request for all of them, and checks every pair within each group.

Most pairs are YES on one market and NO on the other. Sometimes two markets ask opposite questions, such as "Will the Fed cut?" and "Will the Fed hold?". Flag one of them as **reversed** when you add it, and their pairs become YES on both or NO on both.

For each group, the page shows the cheapest pair whose gross edge meets your threshold:

| Setting | Meaning |
| --- | --- |
| Minimum gross edge (¢) | A pair is a candidate when `100 − (ask A + ask B)` is at least this |
| Price buffer per leg (¢) | Added to each leg's ask to form its limit price, so a quote that moves a cent or two still fills |
| Skip manual confirmation | Execute manual pairs without a confirmation dialog |
| Automatic execution | Trade candidates in verified groups without a click |
| Automatic quantity per leg | Contracts per automatic pair |

If a market in a group stops coming back from the exchange, it is excluded from candidates and the activity log says so once. If the quote refresh fails, every candidate is cleared until the next good refresh, so nothing is computed from stale prices.

## Payout verification

Prices can't tell you whether two payouts actually cancel. Here is the same arithmetic on two markets that only look related:

| If the tournament winner is | YES "Spain wins" (20¢) | NO "Argentina wins" (75¢) | The pair pays |
| --- | --- | --- | --- |
| Spain | $1 | $1 | $2 |
| Argentina | $0 | $0 | **$0** |
| Anyone else | $0 | $1 | $1 |

The pair costs 95¢ and usually returns at least $1, but if Argentina wins, both contracts expire worthless. Two prices summing to less than $1 is only free money when the contracts are true complements, and that depends on how each one settles. Reversed flags make the same problem easier to hit, because one wrong flag turns a hedge into a doubled bet.

So automatic execution requires sign-off. A group can trade automatically only after someone has read the markets' rules and pressed **Verify payouts**. Adding a market clears the mark, since the new market hasn't been checked against the others. When a candidate appears in an unverified group, the activity log notes once that it was skipped. Manual execution from the candidate card is always available, and it always shows you the pair first unless you've turned confirmation off.

## Sending the two legs

Kalshi has no way to buy two markets in one atomic step, so a pair is two orders, and things can change in between. If the first fills and the second doesn't (the price moved, the liquidity vanished, the request timed out), what you're left with is an open bet. The order of operations is designed around that:

1. **Leg 1** is a buy at its ask plus the buffer, sent as *immediate-or-cancel*: it fills now, up to the requested quantity, or the rest is dropped.
2. If leg 1 filled nothing, or the request failed in a way that leaves its outcome unknown, **leg 2 is never sent**. An unknown outcome is reported as such, with a reminder to check positions.
3. **Leg 2 is sized to leg 1's actual fill.** Ask for 10, get 4 on leg 1, and leg 2 is an order for 4. A fill below one whole contract stops here and is reported as unhedged.
4. If leg 2 comes back short, the response and the activity log name the market, side, and quantity left **unhedged**.

Only one pair executes at a time. Manual and automatic pairs share a lock, and the work runs in a worker thread so quotes and the rest of the API keep updating meanwhile. Each verified group also has a five-second cooldown between automatic pairs.

Waiting for leg 1's response costs time, and leg 2's price can move in the meantime. The buffer absorbs small moves. Anything larger leaves an unhedged remainder, which the app reports but does not close out for you.

## What a response looks like

`POST /api/arb/execute` returns the same shape for manual pairs, whether live or in dry run:

| Field | Meaning |
| --- | --- |
| `order1`, `order2` | The exchange's response for each leg, or `null` if that leg wasn't sent. In dry run, the order that would have been sent |
| `notes` | Non-error remarks, e.g. `"Leg 1 filled 4/10; leg 2 sized to 4."` |
| `errors` | Why the pair stopped, e.g. `"Leg 1 did not fill (no fill); leg 2 not sent."` |
| `unhedged` | `null`, or `{"ticker", "side", "quantity"}` for exposure left without its opposite leg |
| `dry_run`, `expected_profit` | Present in dry run; profit is gross cents across all pairs |

The README has a [worked dry-run example](../README.md#example-api-call-and-response).
