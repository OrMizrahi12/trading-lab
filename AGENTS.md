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
- Add a dependency: `uv add <pkg>` (updates `pyproject.toml` and `uv.lock`)

## Layout

Top-level scripts (`market_data.py`, `scanner.py`, `orders.py`, ...) are one-off IB experiments. Each needs its own IB client ID: 1 `connect_test.py`, 2 `ibapi_hello.py`, 3 `market_data.py`, 4 `scanner.py`/`orders.py`, 5 `data_pipeline`.

- `data_pipeline/`: incremental download of IB historical bars into a Parquet cache. One file per symbol and bar size (`data/cache/AAPL_5min.parquet`), UTC timestamps, columns `open, high, low, close, volume, average, barCount`. Regular trading hours, TRADES prices. Reuses `Throttle` from `market_data.py`. Read bars with `pd.read_parquet`.
- `tests/`: offline pytest tests.
- `data/`: market data for research. Keep large or raw files out of Git (`data/cache/` is gitignored).
- `strategies/`: strategy definitions and ideas.
- `backtests/`: backtest runs and results.
- `notebooks/`: exploratory Jupyter notebooks.

Update this file when a shared backtesting framework is introduced.

## Git

The default branch is `main`, and the remote is a private GitHub repo (`OrMizrahi12/trading-lab`). `.env` is gitignored.

Commit identity: this repo uses the GitHub no-reply address (`108184274+OrMizrahi12@users.noreply.github.com`, set in `.git/config`) with the name `Or Mizrahi`. This is deliberate, so a personal email never shows up in public commits. Never change `user.email` to a personal address, and never put a personal email in committed files.
