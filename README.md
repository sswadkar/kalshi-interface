# Kalshi workstation

A shared project for manual trading, arbitrage analysis, liquidity monitoring, and
automated liquidity provision, originating from
[sswadkar/kalshi-interface](https://github.com/sswadkar/kalshi-interface).

This replacement imports the expanded shared workstation and organizes it by
feature. The four pages cover manual trading, arbitrage, liquidity monitoring,
and automated quoting. It retains the prototype's trading logic and is not a
production-ready trading engine. The execution and reward-model limitations below
are part of the current release scope.

## Code map

```text
src/kalshi_workstation/
  app.py                       Application composition and router registration
  config.py                    Environment and runtime paths
  runtime.py                   Background-task startup and shutdown
  pages.py                     Four HTML page routes
  features/
    trading/                   Manual orders, dashboard, account history
    arbitrage/                 Groups, analysis, planning, polling, routes
    liquidity_monitor/         Read-only scanning and account order monitoring
    liquidity_provider/        Quote policy, runner, controls
  core/
    exchange/                  HTTP signing, REST, WebSocket, incentive feed
    execution/                 Shared mutation entry point and order helpers
    market_data.py             Market summaries and order-book reconstruction
    liquidity.py               Shared reward-model calculations
    positions.py               Position calculations
    portfolio.py               Account value, history, reward estimates
    prices.py                  Existing conversion and fee helpers
static/                        Existing browser pages
tests/                         Offline unit and integration checks
docs/                          Architecture and offline diagram viewer
server.py                      Compatibility launcher
```

Features use the core; the core does not import features. Runtime order submissions
and cancellations pass through `core/execution/service.py`, which calls the HTTP
adapter. That entry point preserves the old behavior: central risk reservations,
durable order tracking, and uncertain-outcome recovery are still pending. LP still
owns its local inventory and simulation behavior.

The monitor and LP share program metadata supplied by `app.py`, and use shared
reward calculations without importing each other's implementation. Private keys
are loaded on the first authenticated operation, not during import.

## Install and verify offline

Use Python 3.11 or newer. Python 3.11 is the Docker runtime and was used for the
migration checks. `requirements.txt` remains the dependency source;
`pyproject.toml` reads it when installing the package.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check src tests server.py
```

Tests use synthetic data, temporary storage, and mocked exchange operations. They
reject unexpected network calls and need no credentials. They cover the 30-route
contract, order mappings, feature wiring, quote policy, shared program metadata,
import safety, and background-task cleanup. They do not certify strategy economics
or live profitability. GitHub Actions runs the same checks on pull requests and
updates to `main` without exchange credentials.

## Run locally

Create `.env` from `.env.example` only if it does not already exist. Configure a
demo account key ID, a private-key path, and an event ticker. Keep keys outside the
checkout. From an editable source installation:

```bash
python -m uvicorn kalshi_workstation.app:app --host 127.0.0.1 --port 8000
```

`python server.py` and `uvicorn server:app` remain supported. `run.sh` installs the
package and uses the compatibility launcher. The launcher binds to localhost,
and the dashboard/arbitrage dry-run default is now `true`.

Starting the server starts exchange polling and reads persisted strategy settings.
The dashboard/arbitrage and LP dry-run settings remain independent; existing JSON
configuration can enable automated activity. Review settings before starting.
Use one worker: each additional worker would start its own strategy loops.
Operator authentication has not been implemented.

JSON configuration/history files are user-supplied and excluded from Git. Their
default location is the repository root. `KALSHI_DATA_DIR` selects another existing
directory; `KALSHI_ENV_FILE` selects a dotenv file. Relative key paths are resolved
against the project root. No personal account-history backfill is bundled.

The scanner's previous standalone command becomes:

```bash
python -m kalshi_workstation.features.liquidity_monitor.analysis 100
```

This reads the configured exchange; it is not an offline test.

## Architecture and remaining work

Read [the architecture document](docs/architecture.md) on GitHub. For interactive
zoom and diagram switching, download or clone the repository and open
`docs/architecture.html` in a browser. The viewer works offline. Both distinguish
the original prototype, current organization, and proposed reliability work.

Pending work includes inconsistent dry-run/cancellation behavior, uncertain order
outcomes, duplicate fills, stale-data admission, arbitrage payoff/fee validation,
paired-order recovery, and truthful stop reporting. In particular, the existing
reset/cancel routes do not consistently honor the dashboard dry-run setting.
Do not treat that setting as an account-wide safety boundary.

The inherited reward model uses a fixed 200-contract reference depth and independent
side qualification. These assumptions conflict with the reference-depth and
two-sided snapshot-eligibility rules in the
[published incentive-program terms](https://www.cftc.gov/filings/orgrules/rules10142633813.pdf).
Reward estimates and related quote decisions need correction and validation.
The current tests characterize the prototype; they do not certify those rules.

Ignore rules and Docker exclude credentials and runtime data. The former key-file
placeholders have been removed. This branch starts from the public repository's
history and does not import private repository history or personal configuration.

Docker uses the package entry point and copies application sources and static pages.
Supply credentials and persistent data through mounts and environment variables.
Bind any published container port to localhost unless you provide an authenticated
access boundary.

## Attribution

The original public application is by Saraansh Wadkar. The expanded workstation
incorporates work from the shared prototype. Vivek (`vivekv2083`) prepared this
feature-oriented reorganization, migration tests, architecture documentation, and
public replacement. The original MIT license, copyright notice, and public commit
history are retained.
