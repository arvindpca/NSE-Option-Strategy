"""
Angel One SmartAPI helper: login, candles, LTP, scrip master (F&O list + option contracts).
Secrets (GitHub): ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD (PIN), ANGEL_TOTP_SECRET
"""
import os
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import config as C

IST = ZoneInfo("Asia/Kolkata")
REQUEST_GAP = 0.75   # Angel ~3 req/sec, par Scanner + Reliance bhi same account use karte hain
STATS = {"calls": 0, "empty": 0, "rate_limit": 0, "errors": 0, "secs": 0.0, "last_msg": ""}


def _clean(v):
    if v is None:
        return None
    return "".join(ch for ch in v.strip() if unicodedata.category(ch) != "Cf").strip().strip("\"'")


_client = None

try:  # Angel login din me ek baar (session cache)
    from option_bot import angel_session_cache as _session_cache
except Exception:
    try:
        import angel_session_cache as _session_cache
    except Exception:
        _session_cache = None


def login():
    global _client
    import pyotp
    from SmartApi import SmartConnect

    key, cid = _clean(os.getenv("ANGEL_API_KEY")), _clean(os.getenv("ANGEL_CLIENT_ID"))
    pin, totp = _clean(os.getenv("ANGEL_PASSWORD")), _clean(os.getenv("ANGEL_TOTP_SECRET"))
    if not all([key, cid, pin, totp]):
        raise RuntimeError("Angel One secrets missing (ANGEL_API_KEY / CLIENT_ID / PASSWORD / TOTP_SECRET)")
    for attempt in range(1, 4):
        api = SmartConnect(api_key=key)
        data = api.generateSession(cid, pin, pyotp.TOTP(totp).now())
        if data.get("status"):
            print("[angel] login OK", flush=True)
            _client = api
            if _session_cache is not None:
                _session_cache.save(api)
            return api
        print(f"[angel] login fail {attempt}/3: {data.get('message')} {data.get('errorcode', '')}", flush=True)
        time.sleep(max(2, 30 - int(time.time()) % 30))
    raise RuntimeError("Angel One login 3 baar fail")


def client():
    global _client
    if _client is None and _session_cache is not None:
        _client = _session_cache.load()
    return _client or login()


def candles(token: str, exchange: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    """OHLCV candles -> DataFrame(time IST, open, high, low, close, volume)."""
    params = {"exchange": exchange, "symboltoken": str(token), "interval": interval,
              "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M")}
    rows = []
    t0 = time.time()
    for attempt in range(3):
        STATS["calls"] += 1
        try:
            resp = client().getCandleData(params)
            if resp and resp.get("status"):
                rows = resp.get("data") or []
                break
            msg = str((resp or {}).get("message", "empty"))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            STATS["errors"] += 1
        STATS["last_msg"] = msg[:150]
        if any(k in msg.lower() for k in ("rate", "too many", "access denied", "exceeding")):
            STATS["rate_limit"] += 1
            time.sleep(3 * (attempt + 1))
            continue
        break
    time.sleep(REQUEST_GAP)
    STATS["secs"] += time.time() - t0
    if not rows:
        STATS["empty"] += 1
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    t = pd.to_datetime(df["time"], utc=False)
    if t.dt.tz is None:
        t = t.dt.tz_localize(IST)
    df["time"] = t.dt.tz_convert(IST)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


def candles_long(token, exchange, interval, start, end, chunk_days=55):
    """Lambe period ke liye tukdon me download."""
    parts, cur = [], start
    while cur < end:
        stop = min(cur + timedelta(days=chunk_days), end)
        parts.append(candles(token, exchange, interval, cur, stop))
        cur = stop
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    return pd.concat(parts).drop_duplicates("time").sort_values("time").reset_index(drop=True)


def ltp(exchange: str, symbol: str, token: str) -> float | None:
    try:
        r = client().ltpData(exchange, symbol, str(token))
        if r and r.get("status"):
            return float(r["data"]["ltp"])
    except Exception as e:  # noqa: BLE001
        print(f"[angel] ltp fail {symbol}: {e}", flush=True)
    return None


# ---------------------------------------------------------------- scrip master
def load_master() -> pd.DataFrame:
    print("[angel] scrip master download...", flush=True)
    data = requests.get(C.SCRIP_MASTER_URL, timeout=120).json()
    m = pd.DataFrame(data)
    for c in ["token", "symbol", "name", "expiry", "strike", "lotsize", "instrumenttype", "exch_seg"]:
        if c not in m:
            m[c] = ""
    return m


def fno_universe(master: pd.DataFrame) -> pd.DataFrame:
    """F&O stocks (OPTSTK wale) + unka NSE cash token.  cols: name, token"""
    opt = master[(master["exch_seg"] == "NFO") & (master["instrumenttype"] == "OPTSTK")]
    names = set(opt["name"].unique())
    eq = master[(master["exch_seg"] == "NSE") & master["symbol"].str.endswith("-EQ")].copy()
    eq["name_eq"] = eq["symbol"].str[:-3]
    eq = eq[eq["name_eq"].isin(names)]
    return eq[["name_eq", "token"]].rename(columns={"name_eq": "name"}).drop_duplicates("name").reset_index(drop=True)


def option_contracts(master: pd.DataFrame) -> pd.DataFrame:
    """Saare stock option contracts: name, symbol, token, expiry(date), strike(Rs), lot, type(CE/PE)."""
    o = master[(master["exch_seg"] == "NFO") & (master["instrumenttype"] == "OPTSTK")].copy()
    o["expiry_d"] = pd.to_datetime(o["expiry"], format="%d%b%Y", errors="coerce").dt.date
    o["strike_rs"] = pd.to_numeric(o["strike"], errors="coerce") / 100.0
    o["lot"] = pd.to_numeric(o["lotsize"], errors="coerce").fillna(0).astype(int)
    o["opt_type"] = o["symbol"].str[-2:]
    o = o.dropna(subset=["expiry_d", "strike_rs"])
    return o[["name", "symbol", "token", "expiry_d", "strike_rs", "lot", "opt_type"]].reset_index(drop=True)
