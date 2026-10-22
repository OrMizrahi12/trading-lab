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
- `strategies/bollinger_rsi_reversion.py` and `strategies/vwap_stretch_reversion.py`: long-only intraday mean-reversion strategies (specs `strategies/specs/bollinger_rsi_reversion.yaml`, `vwap_stretch_reversion.yaml`), symbol-agnostic. Bollinger: buy a close under the lower 20-bar band (2 standard deviations) with 2-bar RSI under 10, exit when a bar closes at or above the middle band. VWAP stretch: buy a close more than 2 ATR under VWAP, exit when a bar closes at or above VWAP. Both exit as a market sell at the next open (signal exit, not a limit target), use a 2 ATR stop, flatten before the close and take the same optional `no_entry_from` / `no_entry_until` params.
- `strategies/opening_range_breakout.py` (spec `strategies/specs/opening_range_breakout.yaml`): long-only opening range breakout. The range is the high/low of the first `range_minutes` (5 or 15) after 09:30; buy the first 5-minute close above the range high (one entry per day, no re-entry after a stop-out), stop = range low, no target, flatten before the close. `require_above_vwap=True` is the VWAP variant; optional `target_r` sets a profit target that many R (entry minus stop) above the signal close (default none). Symbol-agnostic.
- `tests/`: offline pytest tests. `tests/helpers.py` has the hand-made bar builders used by the trend strategy tests.
- `data/`: market data for research. Keep large or raw files out of Git (`data/cache/` is gitignored).
- `strategies/specs/`: strategy spec YAMLs (`TEMPLATE.yaml` plus one per idea).
- `backtests/`: backtest runs and results. `backtests/vectorized.py` is a simple vectorized EMA(20/50) backtester on SPY 5min (`uv run -m backtests.vectorized`), with look-ahead and cost variants for comparison; not yet the shared engine.
- `backtests/engine.py`: the shared event-driven backtester (`uv run -m backtests.engine` runs `spy_vwap_reclaim` on SPY 5min). `run_backtest(bars, strategy)` feeds closed bars one at a time, fills at the next open, checks stop/target inside each bar via `check_bracket` (stop first, gaps fill at the open), models commission, spread (half per fill) and slippage (market-type fills only) with `CostModel`, and sizes each trade so a stop-out costs 1% of equity including costs, capped at 100% of equity (no leverage; tight stops hit the cap, so real risk is often well under 1%). `r_multiple` is net P&L over a fixed 1% of equity at entry; `stop_risk` is what a stop-out would really cost. Positions are never held overnight (forced exit at the next open). Tests: `tests/test_engine.py`.
- `backtests/sizing.py`: the position sizers the engine accepts through `run_backtest(..., sizer=)`. `FixedFractional` (a stop-out costs 1% of equity, the default), `VolatilityTarget` (one normal day's move, the daily ATR of earlier sessions, costs 1%; the stop is ignored) and `FractionalKelly` (half of the Kelly fraction computed from already-closed trades in R, floored at 0.25% and capped at 2%). The no-leverage cap is applied by the engine after any sizer; with too little history (14 sessions, 30 trades) the history-based sizers fall back to 1% fixed fractional. Tests: `tests/test_sizing.py`.
- `reports/sizing_comparison.py`: ORB on QQQ 5min with the 15-minute range under the three sizers; writes `reports/sizing_comparison.html` (table plus equity chart; `uv run -m reports.sizing_comparison`, under a minute).
- `backtests/trend_comparison.py`: runs `donchian_breakout` and `ema_trend` on SPY and QQQ 5min, all day vs. no entries 11:30-14:00 (8 runs, default costs), and prints one table (`uv run -m backtests.trend_comparison`, a few minutes).
- `backtests/reversion_comparison.py`: runs both reversion strategies on SPY and QQQ 5min at zero, usual and double costs (12 runs, `CostModel` scaled) and prints one table (`uv run -m backtests.reversion_comparison`, a few minutes).
- `backtests/orb_comparison.py`: runs `opening_range_breakout` on SPY and QQQ 5min for N = 5 and 15 minutes, plain vs. above-VWAP (8 runs, usual costs) and prints one table (`uv run -m backtests.orb_comparison`, about 3 minutes).
- `backtests/portfolio.py`: `run_portfolio(sleeves, ...)` runs several (symbol, strategy) sleeves on ONE account with the same fill, cost and R rules as `engine.py` (a one-sleeve run with the rules off matches `run_backtest` exactly). Optional `daily_loss_limit` (no new entries for the rest of the session once the account is that far under the previous close; open positions are not forced shut) and `max_trades_per_day`. Sleeves share one cash pool with no leverage and are served in list order when they signal on the same bar. Tests: `tests/test_portfolio.py`.
- `backtests/portfolio_comparison.py`: ORB QQQ 15min + Donchian SPY + Bollinger QQQ, daily-result correlations, each alone vs. combined with and without a 2% daily loss limit / 3 trades a day; prints one table (`uv run -m backtests.portfolio_comparison`, about 3 minutes).
- `backtests/optimize.py`: grid search of ORB on QQQ 5min over range length (5-60 min) x profit target (none, 1-10 R), 42 runs in parallel, Sharpe as objective, cells under 100 trades ignored; prints the grid, judges plateau vs. peak (best cell against its 8 neighbours) and writes `reports/optimize_orb_qqq.html` (`uv run -m backtests.optimize`, under a minute).
- `backtests/search_comparison.py`: random search vs. Optuna (TPE) vs. the grid on ORB QQQ 5min, 42 trials each over continuous ranges (range length 5-60 min, target 0.5-10 R, last entry 10:00-15:00), Sharpe with at least 100 trades; prints the best of each, trials needed to get near the grid best, and each winner's Sharpe on the first vs. second half of the data (`uv run -m backtests.search_comparison`, about 9 minutes). Optuna is a regular dependency.
- `backtests/split_validation.py`: honest out-of-sample test of the ORB QQQ grid. Splits the data into tuning (first 12 months), validation (next 6) and holdout (last 6); the grid runs on tuning only, then the single best cell and the best smoothed-plateau cell are run on validation and once on the holdout (`uv run -m backtests.split_validation`, about a minute). Min 50 trades per cell.
- `backtests/metrics.py`: `compute_metrics(trades, equity, start_equity)` gives net profit, CAGR, Sharpe, Sortino (daily returns, 0% risk-free, 252 days), max drawdown, profit factor, expectancy, win rate, average R, exposure and trade count; `METRICS` holds each one's label and plain-English explanation. Tests: `tests/test_metrics.py`.
- `reports/spy_vwap_reclaim.py`: runs the engine and writes `reports/spy_vwap_reclaim.html` (metrics, equity curve, drawdown, monthly returns, trade list; `uv run -m reports.spy_vwap_reclaim`, about a minute). `reports/*.html` is gitignored.
- `reports/reversion_by_day_type.py`: splits both reversion strategies' trades (SPY and QQQ 5min, usual costs) into trend days (the third of sessions with the biggest open-to-close move, per symbol) vs. other days and writes `reports/reversion_by_day_type.html` (`uv run -m reports.reversion_by_day_type`, a few minutes). Trend days are picked with hindsight, so this shows where the edge goes, not a tradable filter.
- `notebooks/`: exploratory Jupyter notebooks.



## Git

The default branch is `main`, and the remote is a private GitHub repo (`OrMizrahi12/trading-lab`). `.env` is gitignored.

Commit identity: this repo uses the GitHub no-reply address (`108184274+OrMizrahi12@users.noreply.github.com`, set in `.git/config`) with the name `Or Mizrahi`. This is deliberate, so a personal email never shows up in public commits. Never change `user.email` to a personal address, and never put a personal email in committed files.
