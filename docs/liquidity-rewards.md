# Liquidity rewards

[← Back to the README](../README.md)

In selected markets, Kalshi's liquidity incentive program splits a daily reward pool among traders whose resting orders keep the order book deep. The liquidity page answers two questions: what are my resting orders earning, and where could a new order earn the most? This page explains the model behind both answers and where it can be wrong.

## How rewards are scored

This is our reading of the program's rules, not an official specification. Each side of the book (YES bids and NO bids) is scored separately:

1. If a side's total resting size is below the program's *target size*, nobody on that side is paid.
2. Walking down from the best bid, every price level gets full weight until cumulative size reaches 200 contracts, including the level that crosses it. Below that level, each cent multiplies the weight by the program's *discount factor*, usually 0.5.
3. Only the top *target size* contracts count: levels below the one where cumulative size reaches the target earn nothing.
4. Your share of the side's pool is your weighted size divided by the total.

### An example

Take a target size of 1,000 and a book with 150 contracts at 70¢ and 1,000 at 50¢. Cumulative size passes 200 at 50¢, so both levels count in full.

Now someone adds 100 contracts at 51¢. The 200 mark is reached one cent higher, and the 1,000 contracts at 50¢ drop to half weight, 500. Nobody at 50¢ changed anything, but their share of the pool shrank. Size near the top of the book earns more, and it can lose weight because of what other people do above it.

## The cost of being near the top

Orders near the top are also the first to be filled, often by someone who knows more than you do: a trader who has seen the news before the book adjusts. A reward of a few cents a day doesn't cover being filled at a stale price once. So the monitor groups your resting orders by event, sorts the most exposed first, and flags each one:

| Flag | Meaning |
| --- | --- |
| Only quote | Yours is the only order at the best price on that side: the most exposed position there is |
| Next to fill | At the best price with nothing queued ahead of it |
| At top | At the best price, behind other orders (the *Queue ahead* column says how many contracts) |

Each row also shows the best price on that side, the remaining size, and the estimated daily reward under the model above. Each event's heading shows the total.

**Requeue** cancels an order and places the same post-only order again, which moves it to the back of its price level. If the market has moved through the price in the meantime, post-only makes the new order fail rather than trade, and the original stays cancelled. Orders placed by automated strategies (identified by their client order ID) are excluded, since they manage their own.

## The scanner

The incentive scanner ranks markets in the program by the estimated daily reward a new order of a given size would earn at the best bid on the cheaper side. You can narrow it by series or by ticker. Without a filter, it fetches order books only for the markets with the largest pools, to save requests. It hides markets where neither side meets its target size. Each row shows the pool, the best bid on each side, whether both sides already meet the target size, which side is cheaper to quote, and a category that roughly ranks exposure to better-informed traders: scheduled macro releases are the least exposed, weather sits in the middle, and news, sports, and politics are the most.

The program list is fetched once and refreshed every five minutes in the background, and the scanner and the order monitor share that copy.

## Where the estimates can be wrong

Treat every figure as an upper bound:

- It assumes the book holds still, and that you keep your current share.
- It splits each market's pool evenly between the YES and NO sides. That's our assumption, not a published rule.
- It follows our reading of the scoring rules above, which Kalshi may apply differently or change.
- Prices are assumed to move in whole cents.
