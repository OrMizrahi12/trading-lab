# AGENTS.md

This file provides guidance to AI coding tools (Claude Code, Codex, etc.) when working with code in this repository.

## Hard rule

**Paper trading only, educational use only** (see `rules.txt` and `README.md`). Never add code that places real orders, connects to a live brokerage account, or handles real money or credentials. Nothing here is financial advice.

## Communication

Explain every trading term in plain English the first time you use it.

## Commands

Python 3.12 managed with [uv](https://docs.astral.sh/uv/). There are no dependencies, tests, linter, or build step configured yet.

- Verify the environment: `uv run check_lab.py`
- Add a dependency: `uv add <pkg>` (updates `pyproject.toml` and `uv.lock`)

## Layout

The repo is currently a scaffold. `data/`, `strategies/`, `backtests/` and `notebooks/` contain only `.gitkeep`; the only code is `check_lab.py`, a smoke test of the environment.

- `data/`: market data for research. Keep large or raw files out of Git.
- `strategies/`: strategy definitions and ideas.
- `backtests/`: backtest runs and results.
- `notebooks/`: exploratory Jupyter notebooks.

No architecture exists beyond this folder split. Update this file when a shared backtesting framework or data format is introduced.

## Git

The default branch is `main`, and the remote is a private GitHub repo (`OrMizrahi12/trading-lab`). `.env` is gitignored.

Commit identity: this repo uses the GitHub no-reply address (`108184274+OrMizrahi12@users.noreply.github.com`, set in `.git/config`) with the name `Or Mizrahi`. This is deliberate, so a personal email never shows up in public commits. Never change `user.email` to a personal address, and never put a personal email in committed files.
