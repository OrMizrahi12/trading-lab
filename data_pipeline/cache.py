"""One Parquet file per symbol and bar size, bars stored in UTC."""

import os
from pathlib import Path

import pandas as pd

from .config import CACHE_DIR

COLUMNS = ["open", "high", "low", "close", "volume", "average", "barCount"]


def cache_path(symbol: str, bar_size: str, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / f"{symbol}_{bar_size}.parquet"


def read(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if path.exists() else None


def last_timestamp(path: Path) -> pd.Timestamp | None:
    df = read(path)
    return None if df is None or df.empty else df.index.max()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:
        raise ValueError("bars must have timezone-aware timestamps")
    df = df[COLUMNS].copy()
    df.index = df.index.tz_convert("UTC").rename("date")
    return df


def merge_and_write(path: Path, new: pd.DataFrame) -> pd.DataFrame:
    """Merge `new` into the cache (newest copy wins), sort, and write atomically."""
    new = _normalize(new)
    old = read(path)
    merged = new if old is None else pd.concat([old, new])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp)
    os.replace(tmp, path)  # a crash never leaves a half-written cache
    return merged
