# Design decisions

[← Back to the README](../README.md)

Most of these came from asking what happens when a request fails halfway with real money on the line.

## Sending orders

Dry run is on unless `config.json` says exactly `"dry_run": false`; a missing file, a missing key, or a typo all leave it on. Automatic execution starts off, arbitrage groups start unverified, and turning any of those on asks for confirmation.

An order request that times out may or may not have reached the exchange, and retrying it could double the position. So orders are sent exactly once. A gateway error on an order is logged as "outcome unknown" rather than as a failure, since it may well have filled. Reads and cancellations can safely repeat, so those retry with backoff when rate-limited. Every request has a connect and read timeout, so nothing waits on the exchange forever.

All order submission and cancellation goes through one module, `core/execution/service.py`. Nothing else creates or cancels orders.

## Rate limits

Kalshi's Basic tier allows 20 reads and 10 writes per second, and the balance endpoint allows one request per second with no burst ([rate limits](https://docs.kalshi.com/getting_started/rate_limits)). A rejected read just retries, but a rejected order can't safely be retried (see above). So the client budgets requests before sending them instead of recovering afterwards:

- Every request waits on a client-side limiter ([pyrate-limiter](https://pypi.org/project/pyrate-limiter/)) set below those limits: at most 15 reads and 8 writes in any one-second window by default, and 1 for the balance.
- The balance has the tightest limit and appears on several pages, so one background job fetches it and every page reads the shared copy.
- Quotes for all markets in all arbitrage groups come from one batched request, so watching more markets doesn't raise the request rate.

## Concurrency

Each background job runs as its own asyncio task. The exchange client is synchronous (`requests` with a shared session), so every exchange call inside a job runs in a worker thread through `asyncio.to_thread`. The event loop never waits on the network, and a slow request in one job doesn't delay any other job or API request. Pair execution also runs in a thread, behind a lock, which keeps two pairs from interleaving while the rest of the API stays responsive.

We considered an async HTTP client and decided against it. The order path is short, sequential, and easier to reason about as plain blocking code, and the threads keep it off the event loop anyway.

## Requests from other websites

The server has no login, and any page open in your browser can send requests to `127.0.0.1`. Three checks close that gap:

- State-changing requests that the browser marks as cross-site (via the `Sec-Fetch-Site` and `Origin` headers) are rejected with a 403.
- Requests addressed to unknown host names are rejected, which blocks DNS rebinding.
- Bodies must be JSON. A browser won't send JSON cross-site without first asking the server's permission (a CORS preflight), and this server never grants it.

Requests without browser headers, such as `curl` or scripts, are allowed through. That's why the server must stay bound to loopback.

## Units

Prices and edges are integer cents. The exchange sends prices as dollar strings, which are converted once on arrival, so the arbitrage comparison is exact integer arithmetic. The account balance arrives in integer cents too. The exceptions are display-only: position liquidation values are dollar floats, and fee estimates are fractional cents. Neither feeds an order decision.

## Saved settings

Settings are written to a temporary file, flushed, and atomically renamed into place. Only after that does the running app adopt them, so a failed write (a full disk, say) can't leave automation enabled in memory with nothing saved.

## Structure

Each page is backed by one service object, created at startup in `create_app` and handed to its routes through dependency injection. Background jobs are a list of `(name, factory)` pairs that the runtime starts and stops with the app. Optional add-ons in `kalshi_workstation/extensions` register themselves through a `register(app)` function: they can add routes, background jobs, and a navigation link, and the core never imports one by name.
## Tests

The suite runs offline; any attempt to reach the network fails the test. Most tests cover failure modes:

- leg 2 is withheld after leg 1 fails or its outcome is unknown
- leg 2 is sized to leg 1's fill, and shortfalls are reported as unhedged
- cross-site and non-JSON requests are rejected
- orders are never retried, while reads are
- a failed save can't enable automation
- an unverified group never trades automatically

We checked the key guards by deleting each one and confirming that a test fails.
