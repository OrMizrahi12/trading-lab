# Trading Lab

A personal workspace for learning how trading strategies are designed, tested and evaluated.

**Paper trading only. Educational use only.** Nothing here places real orders or uses real money, and nothing in this repo is financial advice.

## Layout

| Folder | Purpose |
| --- | --- |
| `data/` | Market data used for research (keep large/raw files out of Git) |
| `strategies/` | Strategy definitions and ideas |
| `backtests/` | Backtest runs and results |
| `notebooks/` | Exploratory Jupyter notebooks |

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv run check_lab.py
```

This prints the Python version and today's date to confirm the environment works.
