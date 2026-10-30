#!/usr/bin/env python3
"""
BACKTEST - F&O stock option buying (approx)
===========================================
Same signal rules (option_bot/strategy.py) + same exit rules (option_bot/exits.py).
Option premium Black-Scholes se andaaza (IV = stock ki pichhle 20 din ki volatility x 1.1).

Steps:
  1. Angel One se F&O stocks + Nifty ka 15-min data (BACKTEST_DAYS din, default 120) -> backtest/data/
  2. Signals -> har trade simulate -> portfolio rules (max open, max/din)
  3. backtest/results/<date>/REPORT.txt + trades.csv,  backtest/results/LATEST_REPORT.txt

Env: ANGEL_* secrets, BACKTEST_DAYS=120, MAX_SYMBOLS=0 (0 = sab), OFFLINE=1 (sirf saved data)
"""
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from option_bot import config as C        # noqa: E402
from option_bot import exits as X         # noqa: E402
from option_bot import pricing as P       # noqa: E402
from option_bot import strategy as S      # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DATA = Path(os.getenv("BT_DATA_DIR", str(ROOT / "backtest" / "data")))
OUT = Path(os.getenv("BT_OUT_DIR", str(ROOT / "backtest" / "results")))
DAYS = max(30, min(int(os.getenv("BACKTEST_DAYS", "120")), 365))
IV_MULT = float(os.getenv("IV_MULT", "1.1"))


# ---------------------------------------------------------------- data
def read_csv(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(IST) if pd.to_datetime(df["time"]).dt.tz is None \
        else pd.to_datetime(df["time"]).dt.tz_convert(IST)
    return df


def download():
    from option_bot import angel_api as A
    DATA.mkdir(parents=True, exist_ok=True)
    master = A.load_master()
    uni = A.fno_universe(master)
    lots = A.option_contracts(master).groupby("name")["lot"].max()
    n = int(os.getenv("MAX_SYMBOLS", "0") or 0)
    if n:
        uni = uni.head(n)
    end = datetime.now(IST).replace(second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    print(f"DOWNLOAD {len(uni)} F&O stocks + Nifty | {DAYS} din", flush=True)
    A.candles_long(C.NIFTY_TOKEN, "NSE", "FIFTEEN_MINUTE", start, end).to_csv(DATA / "NIFTY.csv", index=False)
    for i, u in uni.iterrows():
        df = A.candles_long(u["token"], "NSE", "FIFTEEN_MINUTE", start, end)
        if len(df):
            df.to_csv(DATA / f"{u['name']}.csv", index=False)
        if i % 25 == 0:
            print(f"  {i}/{len(uni)}", flush=True)
    pd.DataFrame({"name": lots.index, "lot": lots.values}).to_csv(DATA / "_lots.csv", index=False)


# ---------------------------------------------------------------- one trade
def daily_iv(df: pd.DataFrame) -> pd.Series:
    """Har din ke liye IV andaaza = pichhle 20 din ki daily volatility (annual) x IV_MULT."""
    d = df.groupby("date")["close"].last()
    vol = np.log(d).diff().rolling(20, min_periods=10).std().shift(1) * math.sqrt(252) * getattr(C, "BT_IV_MULT", IV_MULT)
    return vol.clip(0.18, 0.90).fillna(0.30)


def simulate(day: pd.DataFrame, i: int, side: str, iv: float, lot: int):
    """day = ek din ki candles (reset index), i = signal candle.  Entry = signal candle ke close pe."""
    sig = day.iloc[i]
    t_entry = sig["time"] + pd.Timedelta(minutes=15)
    entry_spot = sig["close"]
    delay = int(getattr(C, "BT_ENTRY_DELAY", 0))   # live bot late hota hai: 1 = agli candle ke open pe entry
    if delay:
        if i + delay >= len(day):
            return None
        entry_spot = day.iloc[i + delay]["open"]
        t_entry = day.iloc[i + delay]["time"]
    expiry = P.expiry_for(t_entry.date(), C.MIN_DAYS_TO_EXPIRY)
    step = P.strike_step(sig["close"])
    k = round(sig["close"] / step) * step

    def prem(spot, t):
        return P.bs_price(spot, k, P.years_left(t.to_pydatetime(), expiry), iv, side)

    if iv > getattr(C, "MAX_IV", 9.9):
        return None
    entry = prem(entry_spot, t_entry)
    if entry < C.MIN_PREMIUM or entry * lot > C.MAX_COST_PER_TRADE:
        return None
    pos = {"entry": entry, **X.new_levels(entry)}
    exit_px, reason, t_exit = None, None, None
    for j in range(i + 1 + max(0, delay - 1), len(day)):
        c = day.iloc[j]
        t_end = c["time"] + pd.Timedelta(minutes=15)
        o = entry if (delay and j == i + delay) else prem(c["open"], c["time"])
        hi_spot, lo_spot = (c["high"], c["low"]) if side == "CE" else (c["low"], c["high"])
        h, l = prem(hi_spot, t_end), prem(lo_spot, t_end)   # candle ke end ka time-decay (conservative)
        r = X.step(pos, o, h, l)
        if r:
            reason, exit_px, t_exit = r[0], r[1], t_end
            break
        close_p = prem(c["close"], t_end)
        if C.USE_OR_FAIL_EXIT and S.invalidated(side, c["close"], sig["or_high"], sig["or_low"]):
            reason, exit_px, t_exit = "OR_FAIL", close_p, t_end
            break
        if t_end.strftime("%H:%M") >= C.EXIT_AT:
            reason, exit_px, t_exit = "TIME", close_p, t_end
            break
    if exit_px is None:  # data khatam (aaj ka din adhoora)
        return None
    qty = lot * C.LOTS_PER_TRADE
    return {"sig_time": sig["time"], "entry_time": t_entry, "exit_time": t_exit, "side": side, "strike": k, "expiry": expiry,
            "iv": round(iv, 3), "spot": round(sig["close"], 2), "entry": round(entry, 2), "exit": round(exit_px, 2),
            "reason": reason, "qty": qty, "cost": round(entry * qty), **X.breakdown(entry, exit_px, qty),
            "vol_x": round(sig["volume"] / sig["vol_avg"], 2), "dte": (expiry - t_entry.date()).days,
            "rsi": round(float(sig.get("rsi", float("nan"))), 1) if "rsi" in sig else None}


# ---------------------------------------------------------------- run
R = {"REAL_CHARGES": True}
OLD = {"USE_OR_FAIL_EXIT": True, "VOL_MULT": 1.5, "MAX_COST_PER_TRADE": 30_000}   # v1 rules
NO_ORF = {"USE_OR_FAIL_EXIT": False}
E_ = {**R, **NO_ORF, "VOL_MULT": 2.0, "MAX_COST_PER_TRADE": 20_000}             # live v2
IV = {"MAX_IV": 0.35}
DTE = {"MIN_DAYS_TO_EXPIRY": 10}
RPE = {"RSI_PE_MIN": 25}
G_ = {**E_, **IV, **DTE, **RPE}
GT = {**G_, "TARGET_PCT": 1.00}                                             # v3 candidate
# naam ka pehla shabd = column ka naam (mahine wali table)
VARIANTS = [
    ("A     purane rules v1", {**R, **OLD}),
    ("E     v2 (abhi live)", E_),
    ("G     E + IV/expiry/RSI", G_),
    ("GT    G + target 100% (v3)", GT),
    # target kitna ho? (plateau check)
    ("GT75  target 75%", {**GT, "TARGET_PCT": 0.75}),
    ("GT150 target 150%", {**GT, "TARGET_PCT": 1.50}),
    ("GTnoT target nahi (3:15 tak)", {**GT, "TARGET_PCT": 9.0}),
    ("GTnoBE breakeven band", {**GT, "BREAKEVEN_AT": 9.0}),
    # market breadth: kitne stocks me signal aa chuka
    ("GTb2  >=2 stocks me signal", {**GT, "BT_BREADTH": 2}),
    ("GTb3  >=3 stocks me signal", {**GT, "BT_BREADTH": 3}),
    ("GTb2s >=2 same side signal", {**GT, "BT_BREADTH": 2, "BT_BREADTH_SAME": True}),
    ("GTb3s >=3 same side signal", {**GT, "BT_BREADTH": 3, "BT_BREADTH_SAME": True}),
    ("GTm5  max 5 open, 6/din", {**GT, "MAX_OPEN": 5, "MAX_TRADES_PER_DAY": 6}),
    # STRESS TEST
    ("GTlate late entry 1 candle", {**GT, "BT_ENTRY_DELAY": 1}),
    ("GTiv13 option mehenga IV x1.3", {**GT, "BT_IV_MULT": 1.3}),
    ("GTs2  slippage 2%", {**GT, "SLIPPAGE_PCT": 0.02}),
    ("GTbad late + IV x1.3 + slip 1.5%", {**GT, "BT_ENTRY_DELAY": 1, "BT_IV_MULT": 1.3, "SLIPPAGE_PCT": 0.015}),
]
# Loss analysis 29 Jun - 23 Oct 2026 ke data pe hua tha. Usse PEHLE ka data = "naya data" (rules banate waqt nahi dekha)
SPLIT = pd.Timestamp(os.getenv("SPLIT_DATE", "2026-06-29"), tz=IST)


def compute(files, nifty, lots):
    cands, raw = [], []      # raw = har stock ka din ka pehla signal (filter se pehle) -> market breadth
    for p in files:
        df = read_csv(p)
        if len(df) < 100:
            continue
        sig = S.add_signals(df, nifty)
        ivs = daily_iv(sig)
        lot = int(lots.get(p.stem, 0)) or max(1, int(500_000 / max(df["close"].iloc[-1], 1)))
        for d, day in sig.groupby("date"):
            day = day.reset_index(drop=True)
            idx = day.index[day["signal"] != ""]
            if len(idx) == 0:
                continue
            i = idx[0]                              # ek stock me din ka pehla signal hi
            raw.append((d, day.at[i, "time"], day.at[i, "signal"]))
            tr = simulate(day, i, day.at[i, "signal"], float(ivs.get(d, 0.3)), lot)
            if tr:
                tr["stock"] = p.stem
                cands.append(tr)
    print(f"Signals (trade ban sakte the): {len(cands)}", flush=True)
    if not cands:
        return pd.DataFrame()
    # BREADTH: trade tabhi jab us din (is signal tak) kam se kam N stocks me signal aa chuka ho
    nb = int(getattr(C, "BT_BREADTH", 0))
    if nb > 1:
        rw = pd.DataFrame(raw, columns=["d", "t", "side"])
        same = bool(getattr(C, "BT_BREADTH_SAME", False))
        def ok(c):
            r = rw[(rw["d"] == c["sig_time"].date()) & (rw["t"] <= c["sig_time"])]
            if same:
                r = r[r["side"] == c["side"]]
            return len(r) >= nb
        cands = [c for c in cands if ok(c)]
    # portfolio rules: max open + max trades per day, same time pe zyada volume pehle
    cands.sort(key=lambda x: (x["entry_time"], -x["vol_x"]))
    taken, open_ = [], []
    per_day = {}
    for t in cands:
        open_ = [o for o in open_ if o["exit_time"] > t["entry_time"]]
        d = t["entry_time"].date()
        if len(open_) >= C.MAX_OPEN or per_day.get(d, 0) >= C.MAX_TRADES_PER_DAY:
            continue
        open_.append(t)
        per_day[d] = per_day.get(d, 0) + 1
        taken.append(t)
    if not taken:
        return pd.DataFrame()
    return pd.DataFrame(taken).sort_values("entry_time").reset_index(drop=True)


def run():
    if os.getenv("OFFLINE") != "1":
        download()
    nifty = read_csv(DATA / "NIFTY.csv")
    lots = pd.read_csv(DATA / "_lots.csv").set_index("name")["lot"].to_dict() if (DATA / "_lots.csv").exists() else {}
    files = sorted(p for p in DATA.glob("*.csv") if p.stem not in ("NIFTY", "_lots"))
    base = {k: getattr(C, k) for k in ("USE_OR_FAIL_EXIT", "ENTRY_FROM", "SL_PCT", "TARGET_PCT", "BREAKEVEN_AT",
                                       "VOL_MULT", "MAX_COST_PER_TRADE", "SLIPPAGE_PCT", "REAL_CHARGES",
                                       "MAX_IV", "MIN_DAYS_TO_EXPIRY", "RSI_PE_MIN", "ENTRY_UPTO")}
    base.update(BT_ENTRY_DELAY=0, BT_IV_MULT=IV_MULT, BT_BREADTH=0, BT_BREADTH_SAME=False,
                MAX_OPEN=C.MAX_OPEN, MAX_TRADES_PER_DAY=C.MAX_TRADES_PER_DAY)
    results = []
    for name, changes in VARIANTS:
        for k, v in {**base, **changes}.items():
            setattr(C, k, v)
        print(f"--- {name}", flush=True)
        results.append((name, compute(files, nifty, lots)))
    for k, v in base.items():
        setattr(C, k, v)
    if results[0][1].empty:
        print("Koi trade nahi bana - data / rules check karo")
        return
    live = next((t for n, t in results if "(v3)" in n), results[0][1])
    best = max(results, key=lambda r: r[1]["pnl"].sum() if len(r[1]) else -1e18)
    if best[1] is not live and len(best[1]):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "BEST_LOSS_ANALYSIS.txt").write_text(f"Sabse achha version: {best[0]}\n\n" + "\n".join(loss_analysis(best[1])) + "\n")
    report(live, len(files), nifty, results)


def plus_months(t: pd.DataFrame) -> str:
    if t.empty:
        return "0/0"
    m = t.groupby(t["entry_time"].dt.strftime("%Y-%m"))["pnl"].sum()
    return f"{(m > 0).sum()}/{len(m)}"


def stats(t: pd.DataFrame) -> str:
    if t.empty:
        return "Trades 0"
    wins, losses = t[t["pnl"] > 0]["pnl"].sum(), -t[t["pnl"] <= 0]["pnl"].sum()
    eq = t["pnl"].cumsum()
    dd = (eq - eq.cummax()).min()
    pf = wins / losses if losses else float("inf")
    return (f"Trades {len(t)} | Win {(t['pnl'] > 0).mean() * 100:.1f}% | Kul P&L Rs {t['pnl'].sum():,.0f} | "
            f"Avg Rs {t['pnl'].mean():,.0f} | PF {pf:.2f} | Max DD Rs {dd:,.0f}")


def loss_analysis(tr: pd.DataFrame) -> list:
    t = tr.copy()
    t["hold_min"] = (t["exit_time"] - t["entry_time"]).dt.total_seconds() / 60
    t["day"] = t["entry_time"].dt.day_name().str[:3]
    cut = lambda col, bins, labels: pd.cut(t[col], bins, labels=labels)  # noqa: E731

    def g(key):
        return t.groupby(key, observed=True)["pnl"].agg(
            trades="count", pnl="sum", avg="mean", win_pct=lambda s: (s > 0).mean() * 100).round(0).to_string()
    out = ["=" * 60, "LOSS ANALYSIS (live version)", "=" * 60]
    if {"gross", "slippage", "charges"} <= set(t.columns):
        out += [f"Gross (bina kharche) Rs {t['gross'].sum():,.0f} | Slippage -Rs {t['slippage'].sum():,.0f} | "
                f"Charges -Rs {t['charges'].sum():,.0f} | NET Rs {t['pnl'].sum():,.0f}",
                f"Avg kharcha per trade Rs {(t['slippage'] + t['charges']).mean():,.0f}", ""]
    wins, loss = t[t["pnl"] > 0]["pnl"], t[t["pnl"] <= 0]["pnl"]
    streak = (t["pnl"] <= 0).astype(int).groupby((t["pnl"] > 0).cumsum()).sum().max()
    out += [f"Avg jeet Rs {wins.mean():,.0f} | Avg haar Rs {loss.mean():,.0f} | Sabse badi haar Rs {loss.min():,.0f} | "
            f"Lagatar haar max {streak}", ""]
    out += ["Exit reason:", g("reason"), "", "Side:", g("side"),
            "", "Kitni der trade khula raha (minute):", g(cut("hold_min", [0, 30, 60, 120, 240, 400], ["<30", "30-60", "60-120", "120-240", "240+"])),
            "", "Volume kitna guna:", g(cut("vol_x", [0, 2.5, 3, 4, 6, 1e9], ["2-2.5", "2.5-3", "3-4", "4-6", "6+"])),
            "", "1 lot ki cost (Rs):", g(cut("cost", [0, 5000, 10000, 15000, 20001], ["<5k", "5-10k", "10-15k", "15-20k"])),
            "", "IV (volatility):", g(cut("iv", [0, 0.25, 0.35, 0.5, 1], ["<25%", "25-35%", "35-50%", "50%+"])),
            "", "Expiry me kitne din:", g(cut("dte", [0, 10, 20, 30, 60], ["5-10", "10-20", "20-30", "30+"])),
            "", "Hafte ka din:", g("day")]
    if "rsi" in t and t["rsi"].notna().any():
        t["rsi_b"] = pd.cut(t["rsi"], [0, 25, 35, 45, 55, 65, 75, 100])
        out += ["", "RSI (entry candle):", g("rsi_b")]
    out += ["", "Sabse zyada nuksaan wale stocks (top 10):",
            t.groupby("stock")["pnl"].agg(trades="count", pnl="sum").sort_values("pnl").head(10).round(0).to_string(),
            "", "Sabse zyada munafe wale stocks (top 10):",
            t.groupby("stock")["pnl"].agg(trades="count", pnl="sum").sort_values("pnl", ascending=False).head(10).round(0).to_string()]
    return out


def report(tr: pd.DataFrame, n_stocks: int, nifty: pd.DataFrame, results=()):
    stamp = datetime.now(IST).strftime("%Y-%m-%d_%H%M")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    for n, t in results:   # har version ke trades alag file me (A, B, E, F...)
        t.to_csv(out / f"trades_{n.split()[0].replace('+', '_').replace('@', '_').replace('%', '')}.csv", index=False)
    tr.to_csv(out / "trades.csv", index=False)
    tr["month"] = tr["entry_time"].dt.strftime("%Y-%m")
    tr["hour"] = tr["entry_time"].dt.strftime("%H:00")
    g = lambda col: tr.groupby(col)["pnl"].agg(trades="count", pnl="sum", avg="mean",  # noqa: E731
                                                win_pct=lambda s: (s > 0).mean() * 100).round(0).to_string()
    half = tr["entry_time"].iloc[len(tr) // 2]
    lines = [
        "F&O STOCK OPTION BUYING - BACKTEST (APPROX)",
        "=" * 60,
        f"Stocks {n_stocks} | Data {nifty['time'].min():%Y-%m-%d} se {nifty['time'].max():%Y-%m-%d}",
        f"Rules: ATM option, SL -{C.SL_PCT:.0%}, Target +{C.TARGET_PCT:.0%}, breakeven +{C.BREAKEVEN_AT:.0%}, "
        f"exit {C.EXIT_AT}, max {C.MAX_OPEN} open, {C.MAX_TRADES_PER_DAY}/din, 1 lot <= Rs {C.MAX_COST_PER_TRADE:,}",
        f"Cost Rs {C.COST_PER_TRADE:.0f}/trade + slippage {C.SLIPPAGE_PCT:.0%} | IV = 20d vol x {IV_MULT}",
        "",
        "DHYAN DE: premium formula se andaaza hai (asli option data nahi). Asli me IV badalta hai,",
        "spread hota hai. Isliye pehle 3-4 hafte PAPER trading ka result dekho, phir hi paisa lagao.",
        "",
        "VERSIONS KA MUKABLA (same data, sirf rule badle):",
        *[f"  {n:30s} {stats(t)} | Plus mahine {plus_months(t)}" for n, t in results],
        "",
        f"NAYA DATA (loss analysis me nahi dekha, {SPLIT:%d %b %Y} se pehle):",
        *[f"  {n:30s} {stats(t[t['entry_time'] < SPLIT]) if len(t) else 'Trades 0'}" for n, t in results],
        "",
        f"PURANA DATA (jispe loss analysis hua, {SPLIT:%d %b %Y} ke baad):",
        *[f"  {n:30s} {stats(t[t['entry_time'] >= SPLIT]) if len(t) else 'Trades 0'}" for n, t in results],
        "",
        "Mahine ke hisaab se P&L (Rs):",
        pd.DataFrame({n.split()[0]: t.groupby(t["entry_time"].dt.strftime("%Y-%m"))["pnl"].sum()
                      for n, t in results if len(t)}).fillna(0).round(0).to_string(),
        "", "Neeche ki detail + LOSS ANALYSIS: version GT (v3)", "",
        "KUL:      " + stats(tr),
        "1st half: " + stats(tr[tr["entry_time"] < half]),
        "2nd half: " + stats(tr[tr["entry_time"] >= half]),
        "CALL(CE): " + stats(tr[tr["side"] == "CE"]),
        "PUT(PE):  " + stats(tr[tr["side"] == "PE"]),
        "", "Mahine ke hisaab se:", g("month"),
        "", "Entry time ke hisaab se:", g("hour"),
        "", "Exit reason:", g("reason"),
        "", "Sabse zyada trade wale stocks (top 15):",
        tr.groupby("stock")["pnl"].agg(trades="count", pnl="sum").sort_values("trades", ascending=False)
          .head(15).round(0).to_string(),
        "", *loss_analysis(tr),
    ]
    text = "\n".join(lines) + "\n"
    (out / "REPORT.txt").write_text(text)
    (OUT / "LATEST_REPORT.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    run()
