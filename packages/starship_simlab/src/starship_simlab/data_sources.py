# src/bot/alpha_sim_lab/data_sources.py
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from typing import Optional

import requests


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float


def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_float(s: str) -> Optional[float]:
    try:
        v = float(s)
    except Exception:
        return None
    return v


def fetch_stooq_daily(symbol: str, start: date, end: date) -> list[DailyBar]:
    candidates = [symbol]
    if symbol.startswith("^"):
        candidates.append(symbol[1:])
    else:
        candidates.append("^" + symbol)

    d1 = start.strftime("%Y%m%d")
    d2 = end.strftime("%Y%m%d")

    for sym in candidates:
        url = f"https://stooq.com/q/d/l/?s={sym}&c=0&d1={d1}&d2={d2}&i=d"
        try:
            resp = requests.get(url, timeout=10)
        except Exception:
            continue
        if resp.status_code != 200 or "Date,Open" not in resp.text:
            continue
        return _parse_stooq_csv(resp.text)

    raise RuntimeError(f"No data returned from Stooq for symbol {symbol}")


def fetch_yahoo_daily(symbol: str, start: date, end: date) -> list[DailyBar]:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError(
            "yfinance is required for data_source='yahoo'. Install yfinance."
        ) from exc

    # yfinance end is exclusive; extend by 1 day for inclusive end.
    end_dt = end.toordinal() + 1
    end_inclusive = date.fromordinal(end_dt)

    df = yf.download(
        symbol,
        start=start.isoformat(),
        end=end_inclusive.isoformat(),
        progress=False,
        auto_adjust=False,
        group_by="column",
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"No data returned from Yahoo for symbol {symbol}. "
            "If Yahoo is blocked, provide --input-csv/--vix-csv or use a different data source."
        )

    rows: list[DailyBar] = []
    open_col = df["Open"]
    high_col = df["High"]
    low_col = df["Low"]
    close_col = df["Close"]

    for idx in df.index:
        d = idx.date()
        try:
            o_val = open_col.loc[idx]
            h_val = high_col.loc[idx]
            l_val = low_col.loc[idx]
            c_val = close_col.loc[idx]
            o = float(o_val.iloc[0]) if hasattr(o_val, "iloc") else float(o_val)
            h = float(h_val.iloc[0]) if hasattr(h_val, "iloc") else float(h_val)
            l = float(l_val.iloc[0]) if hasattr(l_val, "iloc") else float(l_val)
            c = float(c_val.iloc[0]) if hasattr(c_val, "iloc") else float(c_val)
        except Exception:
            continue
        rows.append(DailyBar(date=d, open=o, high=h, low=l, close=c))
    return rows


def _parse_stooq_csv(text: str) -> list[DailyBar]:
    rows: list[DailyBar] = []
    reader = csv.DictReader(StringIO(text))
    for row in reader:
        d = _parse_date(row.get("Date", ""))
        o = _parse_float(row.get("Open", ""))
        h = _parse_float(row.get("High", ""))
        l = _parse_float(row.get("Low", ""))
        c = _parse_float(row.get("Close", ""))
        if not d or o is None or h is None or l is None or c is None:
            continue
        rows.append(DailyBar(date=d, open=o, high=h, low=l, close=c))
    return rows


def load_daily_csv(path: str) -> list[DailyBar]:
    rows: list[DailyBar] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = _parse_date(row.get("Date", ""))
            o = _parse_float(row.get("Open", ""))
            h = _parse_float(row.get("High", ""))
            l = _parse_float(row.get("Low", ""))
            c = _parse_float(row.get("Close", ""))
            if not d or o is None or h is None or l is None or c is None:
                continue
            rows.append(DailyBar(date=d, open=o, high=h, low=l, close=c))
    return rows
