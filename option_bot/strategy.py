"""
Signal logic - backtest aur live DONO yahi file use karte hain (taaki rules same rahein).

Input DataFrame (15-min candles, IST):  time, open, high, low, close, volume
"""
import numpy as np
import pandas as pd

from . import config as C


def _rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators + opening range + VWAP + prev close jodo."""
    df = df.sort_values("time").reset_index(drop=True).copy()
    df["date"] = df["time"].dt.date
    df["hhmm"] = df["time"].dt.strftime("%H:%M")

    df["ema_f"] = df["close"].ewm(span=C.EMA_FAST, adjust=False).mean()
    df["ema_s"] = df["close"].ewm(span=C.EMA_SLOW, adjust=False).mean()
    df["rsi"] = _rsi(df["close"], C.RSI_LEN)
    # pichhli 20 candle ka average volume (current candle shamil nahi)
    df["vol_avg"] = df["volume"].shift(1).rolling(C.VOL_LOOKBACK, min_periods=10).mean()

    # VWAP (har din naya)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).groupby(df["date"]).cumsum()
    vv = df["volume"].groupby(df["date"]).cumsum()
    df["vwap"] = (pv / vv.replace(0, np.nan)).fillna(df["close"])

    # Opening range: 09:15 <= candle start < 09:45
    in_or = (df["hhmm"] >= C.OR_START) & (df["hhmm"] < C.OR_END)
    orh = df["high"].where(in_or).groupby(df["date"]).transform("max")
    orl = df["low"].where(in_or).groupby(df["date"]).transform("min")
    df["or_high"], df["or_low"] = orh, orl

    # Pichhle din ka close + aaj ka open
    day_close = df.groupby("date")["close"].last()
    prev_close = day_close.shift(1)
    df["prev_close"] = df["date"].map(prev_close)
    df["day_open"] = df.groupby("date")["open"].transform("first")
    df["gap_pct"] = (df["day_open"] / df["prev_close"] - 1) * 100
    df["chg_pct"] = (df["close"] / df["prev_close"] - 1) * 100
    return df


def nifty_bias(nifty: pd.DataFrame) -> pd.DataFrame:
    """Nifty ka mood har 15-min candle pe: UP / DOWN / FLAT  (+ Nifty % change)."""
    n = nifty.sort_values("time").reset_index(drop=True).copy()
    n["date"] = n["time"].dt.date
    n["ema_s"] = n["close"].ewm(span=C.EMA_SLOW, adjust=False).mean()
    n["day_open"] = n.groupby("date")["open"].transform("first")
    prev = n.groupby("date")["close"].last().shift(1)
    n["n_chg_pct"] = (n["close"] / n["date"].map(prev) - 1) * 100
    up = (n["close"] > n["day_open"]) & (n["close"] > n["ema_s"])
    dn = (n["close"] < n["day_open"]) & (n["close"] < n["ema_s"])
    n["n_bias"] = np.where(up, "UP", np.where(dn, "DOWN", "FLAT"))
    return n[["time", "n_bias", "n_chg_pct"]]


def add_signals(df: pd.DataFrame, nifty: pd.DataFrame | None) -> pd.DataFrame:
    """Har candle pe 'signal' column: 'CE', 'PE' ya ''."""
    df = prepare(df)
    if nifty is not None and len(nifty):
        df = df.merge(nifty_bias(nifty), on="time", how="left")
    else:
        df["n_bias"], df["n_chg_pct"] = "FLAT", 0.0
    df["n_bias"] = df["n_bias"].fillna("FLAT")
    df["n_chg_pct"] = df["n_chg_pct"].fillna(0.0)

    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs() / rng
    strong = body.fillna(0) >= C.MIN_BODY_PCT
    vol_ok = df["volume"] > C.VOL_MULT * df["vol_avg"]
    time_ok = (df["hhmm"] >= C.ENTRY_FROM) & (df["hhmm"] < C.ENTRY_UPTO)
    price_ok = df["close"].between(C.MIN_PRICE, C.MAX_PRICE)
    gap_ok = df["gap_pct"].abs() <= C.MAX_GAP_PCT
    base = strong & vol_ok & time_ok & price_ok & gap_ok & df["or_high"].notna()

    prev_close = df["close"].shift(1)
    same_day = df["date"] == df["date"].shift(1)

    ce = (base
          & (df["close"] > df["or_high"]) & ~(same_day & (prev_close > df["or_high"]))  # fresh breakout
          & (df["close"] > df["open"])
          & (df["close"] > df["vwap"]) & (df["ema_f"] > df["ema_s"])
          & df["rsi"].between(C.RSI_CE_MIN, C.RSI_CE_MAX)
          & (df["chg_pct"] > df["n_chg_pct"]))                       # Nifty se zyada strong
    pe = (base
          & (df["close"] < df["or_low"]) & ~(same_day & (prev_close < df["or_low"]))
          & (df["close"] < df["open"])
          & (df["close"] < df["vwap"]) & (df["ema_f"] < df["ema_s"])
          & df["rsi"].between(C.RSI_PE_MIN, C.RSI_PE_MAX)
          & (df["chg_pct"] < df["n_chg_pct"]))
    if C.USE_NIFTY_FILTER:
        ce &= df["n_bias"] == "UP"
        pe &= df["n_bias"] == "DOWN"

    df["signal"] = np.where(ce, "CE", np.where(pe, "PE", ""))
    return df


def invalidated(side: str, close: float, or_high: float, or_low: float) -> bool:
    """Stock wapas Opening Range ke andar band hua -> breakout fail -> exit."""
    return close < or_high if side == "CE" else close > or_low
