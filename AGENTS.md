# AGENTS.md

This file provides guidance to AI coding tools (Claude Code, Codex, etc.) when working with code in this repository.

## Hard rule

**Paper trading only, educational use only** (see `rules.txt` and `README.md`). Never add code that places real orders, connects to a live brokerage account, or handles real money or credentials. Nothing here is financial advice.

## Communication

Explain every trading term in plain English the first time you use it.

## Commands

Python 3.12 managed with [uv](https://docs.astral.sh/uv/). There is no linter or build step configured yet.

- Verify the environment: `uv run check_lab.py`
- Run the offline tests (fake IB, no Gateway needed): `uv run pytest`
- Update the bar cache (needs IB Gateway paper on port 4002): `uv run -m data_pipeline` (`--dry-run` shows the plan without connecting)
- Check the cache for bad or missing data (offline): `uv run -m data_pipeline --quality`
- Add a dependency: `uv add <pkg>` (updates `pyproject.toml` and `uv.lock`)

## Layout

Top-level scripts (`market_data.py`, `scanner.py`, `orders.py`, ...) are one-off IB experiments. Each needs its own IB client ID: 1 `connect_test.py`, 2 `ibapi_hello.py`, 3 `market_data.py`, 4 `scanner.py`/`orders.py`, 5 `data_pipeline`.

- `data_pipeline/`: incremental download of IB historical bars into a Parquet cache. One file per symbol and bar size (`data/cache/AAPL_5min.parquet`), UTC timestamps, columns `open, high, low, close, volume, average, barCount`. Regular trading hours, TRADES prices. Reuses `Throttle` from `market_data.py`. Read bars with `pd.read_parquet`. `data_pipeline/quality.py` checks each file against the NYSE calendar (`exchange_calendars`).
- `indicators.py`: SMA, EMA, RSI (Wilder), ATR, Bollinger Bands and session-reset VWAP, from scratch with pandas, no look-ahead. Matches TA-Lib conventions (TA-Lib is a dev-only dependency used by the tests).
- `strategies/`: the shared Strategy interface (`base.py`) and strategy code. A strategy is a pure function `on_bar(bars, position) -> Entry | Exit | None`: it sees closed bars only, decides at the bar close, and the engine fills at the next open. The backtester (module 6) and the live paper trader both call it, so a strategy is written once. `check_bracket` resolves stop/target hits (stop first when a bar touches both). `strategies/spy_vwap_reclaim.py` implements `strategies/specs/spy_vwap_reclaim.yaml`; its params dataclass mirrors the spec (no YAML loader yet). Last-entry and flatten times are offsets from each day's real NYSE close (half days included). Position sizing, costs and fills belong to the engine, not the strategy.
- `strategies/donchian_breakout.py` and `strategies/ema_trend.py`: long-only intraday trend strategies (specs `strategies/specs/donchian_breakout.yaml`, `ema_trend.yaml`), symbol-agnostic so SPY and QQQ use the same code. Donchian: buy a close above the prior 20 bars' high, stop = tighter of the 20-bar low and 2 ATR, no target. EMA trend: enter when EMA20 > EMA50 and close > VWAP switch on, exit when either fails, 2 ATR stop. Both flatten before the close and take optional `no_entry_from` / `no_entry_until` params (midday filter, judged on the fill time). `strategies/session_clock.py` holds the shared close-offset and no-entry-window helpers (`spy_vwap_reclaim` still has its own copy).
- `tests/`: offline pytest tests. `tests/helpers.py` has the hand-made bar builders used by the trend strategy tests.
- `data/`: market data for research. Keep large or raw files out of Git (`data/cache/` is gitignored).
- `strategies/specs/`: strategy spec YAMLs (`TEMPLATE.yaml` plus one per idea).
- `backtests/`: backtest runs and results. `backtests/vectorized.py` is a simple vectorized EMA(20/50) backtester on SPY 5min (`uv run -m backtests.vectorized`), with look-ahead and cost variants for comparison; not yet the shared engine.
- `backtests/engine.py`: the shared event-driven backtester (`uv run -m backtests.engine` runs `spy_vwap_reclaim` on SPY 5min). `run_backtest(bars, strategy)` feeds closed bars one at a time, fills at the next open, checks stop/target inside each bar via `check_bracket` (stop first, gaps fill at the open), models commission, spread (half per fill) and slippage (market-type fills only) with `CostModel`, and sizes each trade so a stop-out costs 1% of equity including costs, capped at 100% of equity (no leverage; tight stops hit the cap, so real risk is often well under 1%). `r_multiple` is net P&L over a fixed 1% of equity at entry; `stop_risk` is what a stop-out would really cost. Positions are never held overnight (forced exit at the next open). Tests: `tests/test_engine.py`.
- `backtests/trend_comparison.py`: runs `donchian_breakout` and `ema_trend` on SPY and QQQ 5min, all day vs. no entries 11:30-14:00 (8 runs, default costs), and prints one table (`uv run -m backtests.trend_comparison`, a few minutes).
- `backtests/metrics.py`: `compute_metrics(trades, equity, start_equity)` gives net profit, CAGR, Sharpe, Sortino (daily returns, 0% risk-free, 252 days), max drawdown, profit factor, expectancy, win rate, average R, exposure and trade count; `METRICS` holds each one's label and plain-English explanation. Tests: `tests/test_metrics.py`.
- `reports/spy_vwap_reclaim.py`: runs the engine and writes `reports/spy_vwap_reclaim.html` (metrics, equity curve, drawdown, monthly returns, trade list; `uv run -m reports.spy_vwap_reclaim`, about a minute). `reports/*.html` is gitignored.
- `notebooks/`: exploratory Jupyter notebooks.



## Git

The default branch is `main`, and the remote is a private GitHub repo (`OrMizrahi12/trading-lab`). `.env` is gitignored.

Commit identity: this repo uses the GitHub no-reply address (`108184274+OrMizrahi12@users.noreply.github.com`, set in `.git/config`) with the name `Or Mizrahi`. This is deliberate, so a personal email never shows up in public commits. Never change `user.email` to a personal address, and never put a personal email in committed files.
