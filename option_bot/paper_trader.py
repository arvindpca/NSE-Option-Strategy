#!/usr/bin/env python3
"""
LIVE PAPER TRADER  (koi asli order nahi lagta)
==============================================
GitHub Actions har ~15 min market time me chalata hai. Har run me:
  1. Khuli paper positions check -> SL / Target / Breakeven / Breakout fail / 3:15 exit
  2. Naye signal dhoondo (F&O stocks, 15-min) -> option chuno -> paper entry
  3. Telegram alert + state/ folder me log (repo me commit hota hai)

State files (state/):
  positions.json     abhi khule trades
  trades.csv         band trades (P&L ke saath)
  signals_log.csv    har signal (liya / kyun chhoda)
  meta.json          aaj ke counters
  STATUS.md          readable summary

Env: ANGEL_* secrets, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
     FORCE_RUN=1 (market time ke bahar bhi chalao - testing)
     MAX_SYMBOLS=20 (testing ke liye kam stocks)
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from option_bot import angel_api as A          # noqa: E402
from option_bot import config as C             # noqa: E402
from option_bot import exits as X              # noqa: E402
from option_bot import strategy as S           # noqa: E402
from option_bot.option_picker import pick_contract  # noqa: E402
from option_bot.telegram import send           # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
STATE = Path(os.getenv("STATE_DIR", str(ROOT / "state")))
POS_F, TRADES_F, SIG_F = STATE / "positions.json", STATE / "trades.csv", STATE / "signals_log.csv"
META_F, STATUS_F = STATE / "meta.json", STATE / "STATUS.md"

TRADE_COLS = ["date", "stock", "side", "option", "expiry", "strike", "lot", "qty", "entry_time", "entry",
              "exit_time", "exit", "reason", "gross", "slippage", "charges", "pnl", "spot_entry"]
SIG_COLS = ["time", "stock", "side", "spot", "vol_x", "action", "note"]


RUN = {}   # is run ki chhoti report (state/last_run.md)


# ---------------------------------------------------------------- small helpers
def hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def inr(x: float) -> str:
    s = f"{abs(x):,.0f}"
    return ("-" if x < 0 else "+") + "Rs " + s


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return default


def append_csv(path: Path, cols, row: dict):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})


def completed(df: pd.DataFrame, now: datetime, minutes: int) -> pd.DataFrame:
    """Sirf poori ho chuki candles (start + interval <= abhi)."""
    if df.empty:
        return df
    return df[df["time"] + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)]


# ---------------------------------------------------------------- exits
def close_position(pos, price, reason, now, trades_today):
    qty = pos["qty"]
    b = X.breakdown(pos["entry"], price, qty)
    p = b["pnl"]
    row = {**pos, **b, "exit_time": now.strftime("%Y-%m-%d %H:%M"), "exit": round(price, 2), "reason": reason}
    append_csv(TRADES_F, TRADE_COLS, row)
    emoji = {"TARGET": "✅", "SL": "🛑", "BREAKEVEN": "⚪", "OR_FAIL": "↩️", "TIME": "⏰"}.get(reason, "•")
    send(f"{emoji} <b>PAPER EXIT</b> {pos['option']}\n"
         f"{pos['stock']} {pos['side']} | Entry {pos['entry']} → Exit {round(price, 2)}\n"
         f"Reason: {reason} ({qty} qty)\n"
         f"Gross {inr(b['gross'])} | Kharcha -Rs {b['slippage'] + b['charges']:,.0f}\n"
         f"Net P&L: <b>{inr(p)}</b>")
    trades_today.append(p)
    print(f"EXIT {pos['option']} {reason} {pos['entry']}->{price:.2f} pnl {p}", flush=True)


def manage_open(positions, now, today_trades_pnl):
    still = []
    for pos in positions:
        last = datetime.fromisoformat(pos["last_check"])
        oc = A.candles(pos["opt_token"], "NFO", "FIVE_MINUTE", last - timedelta(minutes=5), now)
        oc = completed(oc, now, 5)
        oc = oc[oc["time"] >= pd.Timestamp(last)]
        done = None
        for _, c in oc.iterrows():
            r = X.step(pos, c["open"], c["high"], c["low"])
            if r:
                done = (r[1], r[0])
                break
        if not done and C.USE_OR_FAIL_EXIT:
            # Stock wapas Opening Range ke andar band? (pichhli poori 15-min candle)
            uc = A.candles(pos["stock_token"], "NSE", "FIFTEEN_MINUTE",
                           now.replace(hour=9, minute=15, second=0, microsecond=0), now)
            uc = completed(uc, now, 15)
            uc = uc[uc["time"] > pd.Timestamp(datetime.fromisoformat(pos["entry_candle"]))]
            if len(uc) and S.invalidated(pos["side"], uc["close"].iloc[-1], pos["or_high"], pos["or_low"]):
                px = A.ltp("NFO", pos["option"], pos["opt_token"]) or (oc["close"].iloc[-1] if len(oc) else pos["entry"])
                done = (px, "OR_FAIL")
        if not done and hm(now) >= C.EXIT_AT:
            px = A.ltp("NFO", pos["option"], pos["opt_token"]) or (oc["close"].iloc[-1] if len(oc) else pos["entry"])
            done = (px, "TIME")
        if done:
            close_position(pos, done[0], done[1], now, today_trades_pnl)
        else:
            pos["last_check"] = (oc["time"].iloc[-1] + pd.Timedelta(minutes=5)).isoformat() if len(oc) else pos["last_check"]
            still.append(pos)
    return still


# ---------------------------------------------------------------- entries
def scan(now, universe, contracts, positions, meta):
    today = now.date()
    start = now - timedelta(days=5)          # indicators ke warm-up ke liye pichhle din bhi
    nifty = pd.DataFrame()
    for attempt in range(3):            # Nifty na mile to poora scan skip hota tha -> 3 baar try
        nifty = completed(A.candles(C.NIFTY_TOKEN, "NSE", "FIFTEEN_MINUTE", start, now), now, 15)
        if not nifty.empty:
            break
        time.sleep(5 * (attempt + 1))
    if nifty.empty:
        print("Nifty data nahi mila - scan skip", flush=True)
        return []
    # Nifty ka mood (pichhli 2 candle): kaunsi side ka signal aa sakta hai?
    nb = S.nifty_bias(nifty)
    moods = set(nb["n_bias"].tail(C.SIGNAL_LOOKBACK_CANDLES))
    sides = {"CE", "PE"} if not C.USE_NIFTY_FILTER else ({"CE"} if "UP" in moods else set()) | ({"PE"} if "DOWN" in moods else set())
    RUN["nifty_mood"] = ",".join(sorted(moods))
    if not sides:
        print(f"Nifty FLAT ({moods}) - koi signal possible nahi, scan skip (Angel calls bache)", flush=True)
        RUN["scanned"] = 0
        return []
    if C.PREFILTER:
        universe = prefilter(universe, sides, float(nb["n_chg_pct"].iloc[-1]))
    found = []
    t0 = time.time()
    budget = float(os.getenv("SCAN_BUDGET_SEC", "540"))   # 9 min: job 14 min me kabhi na atke
    RUN["scanned"] = 0
    # Angel rate-limit ki wajah se ek run me sab stocks nahi ho paate:
    # har run pichhle run ke aage se shuru kare, taaki din me sab cover ho.
    off = int(meta.get("scan_offset", 0)) % max(1, len(universe))
    universe = pd.concat([universe.iloc[off:], universe.iloc[:off]])
    RUN["start_at"] = off
    for n, (_, u) in enumerate(universe.iterrows(), 1):
        if time.time() - t0 > budget:
            print(f"scan time khatam: {n-1}/{len(universe)} stocks dekhe", flush=True)
            RUN["scan_cut"] = True
            break
        if n % 25 == 0:
            print(f"[scan] {n}/{len(universe)} | {time.time()-t0:.0f}s | {A.STATS}", flush=True)
        RUN["scanned"] = n
        df = completed(A.candles(u["token"], "NSE", "FIFTEEN_MINUTE", start, now), now, 15)
        if len(df) < 30 or df["time"].iloc[-1].date() != today:
            continue
        sig = S.add_signals(df, nifty)
        recent = sig[sig["date"] == today].tail(C.SIGNAL_LOOKBACK_CANDLES)
        hit = recent[recent["signal"] != ""]
        if hit.empty:
            continue
        r = hit.iloc[-1]
        # breakout abhi bhi zinda hai? (aakhri candle OR ke bahar)
        last_close = sig["close"].iloc[-1]
        if S.invalidated(r["signal"], last_close, r["or_high"], r["or_low"]):
            continue
        found.append({"stock": u["name"], "token": u["token"], "side": r["signal"], "spot": last_close,
                      "candle": r["time"], "or_high": r["or_high"], "or_low": r["or_low"],
                      "vol_x": round(r["volume"] / r["vol_avg"], 2)})
    found.sort(key=lambda x: -x["vol_x"])   # sabse zyada volume uchhal pehle
    meta["scan_offset"] = (off + RUN.get("scanned", 0)) % max(1, len(universe))
    RUN["scan_secs"] = round(time.time() - t0)
    RUN["signals"] = [f"{f['stock']} {f['side']}" for f in found]
    return found


def prefilter(universe, sides, n_chg):
    """Batch quote (5 calls) se wo stocks chhaato jinme signal ho hi nahi sakta.
    CE ke liye zaroori: abhi ka daam din ke open se upar + Nifty se zyada strong.  PE ulta.
    Thodi chhoot (0.3% / 0.5%) rakhi hai kyunki signal 1-2 candle pehle ka ho sakta hai."""
    q = A.quotes(list(universe["token"]))
    if not q:
        RUN["prefilter"] = "fail -> sab stocks"
        return universe

    def maybe(tok):
        d = q.get(str(tok))
        if not d or not d["ltp"] or not d["close"] or not d["open"]:
            return True                       # data nahi -> scan karo (safe)
        chg = (d["ltp"] / d["close"] - 1) * 100
        ce = "CE" in sides and d["ltp"] > d["open"] * 0.997 and chg > n_chg - 0.5
        pe = "PE" in sides and d["ltp"] < d["open"] * 1.003 and chg < n_chg + 0.5
        return ce or pe
    keep = universe[universe["token"].map(maybe)]
    RUN["prefilter"] = f"{len(keep)}/{len(universe)} stocks"
    print(f"prefilter: {len(keep)}/{len(universe)} stocks scan honge (sides {sides})", flush=True)
    return keep


def est_iv(token, now):
    """Backtest jaisa IV andaaza: pichhle 20 din ki daily volatility (annual) x IV_MULT, 18%-90%."""
    import math
    df = A.candles(token, "NSE", "ONE_DAY", now - timedelta(days=45), now)
    if df.empty:
        return None
    d = df.assign(d=df["time"].dt.date).groupby("d")["close"].last()
    d = d[d.index < now.date()]                # aaj ka din shamil nahi
    r = (d / d.shift(1)).apply(lambda x: math.log(x) if x and x > 0 else float("nan")).dropna().tail(20)
    if len(r) < 10:
        return None
    return min(max(float(r.std()) * math.sqrt(252) * C.IV_MULT, 0.18), 0.90)


def try_enter(cand, now, contracts, positions, meta):
    log = {"time": now.strftime("%Y-%m-%d %H:%M"), "stock": cand["stock"], "side": cand["side"],
           "spot": round(cand["spot"], 2), "vol_x": cand["vol_x"]}
    key = f"{cand['stock']}|{cand['side']}"
    if cand["stock"] in meta["stocks_today"]:
        return None  # aaj is stock me already trade / signal ho chuka - dobara log nahi
    meta["stocks_today"].append(cand["stock"])

    def skip(note):
        append_csv(SIG_F, SIG_COLS, {**log, "action": "SKIP", "note": note})
        print(f"SKIP {key}: {note}", flush=True)

    def retry_later(note):
        # Data hi nahi aaya (Angel rate-limit) -> stock ko aaj ke liye block mat karo, agle run me dobara.
        # Par 2 baar se zyada nahi (sach me 0 volume wala option roz API na khaye).
        tries = meta.setdefault("retries", {})
        tries[cand["stock"]] = tries.get(cand["stock"], 0) + 1
        if tries[cand["stock"]] > 2:
            return skip(note + " - 3 baar, ab skip")
        meta["stocks_today"].remove(cand["stock"])
        append_csv(SIG_F, SIG_COLS, {**log, "action": "RETRY", "note": note})
        print(f"RETRY {key}: {note}", flush=True)

    if len(positions) >= C.MAX_OPEN:
        return skip(f"max {C.MAX_OPEN} position khuli")
    if meta["trades"] >= C.MAX_TRADES_PER_DAY:
        return skip(f"aaj ke {C.MAX_TRADES_PER_DAY} trade ho gaye")
    con = pick_contract(contracts, cand["stock"], cand["spot"], cand["side"], now.date())
    if con is None:
        return skip("option contract nahi mila")
    iv = est_iv(cand["token"], now)
    if iv is None:
        return retry_later("IV ke liye daily data nahi mila")
    log["note_iv"] = round(iv, 2)
    if iv > C.MAX_IV:
        return skip(f"stock bahut uchhalta hai (IV andaaza {iv:.0%} > {C.MAX_IV:.0%})")
    day_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    oc = A.candles(con["token"], "NFO", "FIVE_MINUTE", day_start, now)
    if oc.empty:
        time.sleep(3)
        oc = A.candles(con["token"], "NFO", "FIVE_MINUTE", day_start, now)
    if oc.empty:
        return retry_later("option candle data nahi mila")
    opt_vol = float(oc["volume"].sum()) if len(oc) else 0.0
    price = A.ltp("NFO", con["symbol"], con["token"]) or (float(oc["close"].iloc[-1]) if len(oc) else None)
    if not price and oc.empty:
        return retry_later("option ka daam/data nahi mila")
    if not price or price < C.MIN_PREMIUM:
        return skip(f"premium {price} bahut kam / nahi mila")
    # Angel NFO candle volume LOTS (contracts) me deta hai, shares me nahi (28 Oct check: RELIANCE
    # candle_sum x lot ~= marketData tradeVolume). Isliye seedha lots se compare.
    if opt_vol < C.MIN_OPTION_VOL_LOTS:
        return skip(f"option illiquid (aaj sirf {opt_vol:.0f} lots, chahiye {C.MIN_OPTION_VOL_LOTS})")
    qty = int(con["lot"]) * C.LOTS_PER_TRADE
    cost = price * qty
    if cost > C.MAX_COST_PER_TRADE:
        return skip(f"1 lot ki cost Rs {cost:,.0f} > Rs {C.MAX_COST_PER_TRADE:,}")

    lv = X.new_levels(price)
    pos = {"date": now.strftime("%Y-%m-%d"), "stock": cand["stock"], "stock_token": cand["token"],
           "side": cand["side"], "option": con["symbol"], "opt_token": con["token"],
           "expiry": str(con["expiry_d"]), "strike": float(con["strike_rs"]), "lot": int(con["lot"]),
           "qty": qty, "entry_time": now.strftime("%Y-%m-%d %H:%M"), "entry": round(price, 2),
           "sl": lv["sl"], "target": lv["target"], "spot_entry": round(cand["spot"], 2),
           "or_high": float(cand["or_high"]), "or_low": float(cand["or_low"]),
           "entry_candle": pd.Timestamp(cand["candle"]).isoformat(), "last_check": now.isoformat()}
    append_csv(SIG_F, SIG_COLS, {**log, "action": "ENTRY", "note": con["symbol"]})
    meta["trades"] += 1
    arrow = "🟢 BUY CALL" if cand["side"] == "CE" else "🔴 BUY PUT"
    send(f"{arrow} <b>(PAPER)</b>\n<b>{con['symbol']}</b>\n"
         f"Stock: {cand['stock']} @ {cand['spot']:.2f} | Vol {cand['vol_x']}x\n"
         f"Premium: <b>{price:.2f}</b> | Lot {con['lot']} | Cost Rs {cost:,.0f}\n"
         f"SL {lv['sl']} (-{int(C.SL_PCT*100)}%) | Target {lv['target']} (+{int(C.TARGET_PCT*100)}%)\n"
         f"IV andaaza {iv:.0%} | Exit 3:15 PM tak" + (" | Breakout fail = exit" if C.USE_OR_FAIL_EXIT else ""))
    print(f"ENTRY {con['symbol']} @ {price}", flush=True)
    return pos


# ---------------------------------------------------------------- status
def write_status(positions, now):
    lines = [f"# Option Paper Trader - status ({now:%d %b %Y %H:%M} IST)", ""]
    lines.append(f"## Khuli positions ({len(positions)})")
    if positions:
        lines.append("| Option | Side | Entry | SL | Target | Time |\n|---|---|---|---|---|---|")
        for p in positions:
            lines.append(f"| {p['option']} | {p['side']} | {p['entry']} | {p['sl']} | {p['target']} | {p['entry_time'][11:]} |")
    else:
        lines.append("Koi nahi")
    if TRADES_F.exists():
        t = pd.read_csv(TRADES_F)
        if len(t):
            wins = (t["pnl"] > 0).mean() * 100
            g, sl, ch, net = (t[c].sum() for c in ("gross", "slippage", "charges", "pnl"))
            lines += ["", "## Ab tak ka result (v3, 2 Nov 2026 se)",
                      f"Trades {len(t)} | Win {wins:.0f}% | Avg net {inr(t['pnl'].mean())}", "",
                      "| | Rs |\n|---|---|",
                      f"| Gross P&L (bina kharche) | {inr(g)} |",
                      f"| Slippage | -Rs {sl:,.0f} |",
                      f"| Charges (brokerage+STT+GST..) | -Rs {ch:,.0f} |",
                      f"| **Net P&L** | **{inr(net)}** |", ""]
            d = t.groupby("date")[["gross", "pnl"]].sum()
            lines += ["## Din ke hisaab se", "| Date | Trades | Gross | Net |\n|---|---|---|---|"]
            for dt, r in d.iloc[::-1].iterrows():
                lines.append(f"| {dt} | {(t['date'] == dt).sum()} | {r['gross']:,.0f} | {r['pnl']:,.0f} |")
            lines += ["", "## Aakhri 15 trades",
                      "| Date | Option | Reason | Entry | Exit | Gross | Net |\n|---|---|---|---|---|---|---|"]
            for _, r in t.tail(15).iloc[::-1].iterrows():
                lines.append(f"| {r['date']} | {r['option']} | {r['reason']} | {r['entry']} | {r['exit']} "
                             f"| {r['gross']:.0f} | {r['pnl']:.0f} |")
    STATUS_F.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- main
def main():
    STATE.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST).replace(second=0, microsecond=0)
    if os.getenv("AS_OF"):  # testing
        now = datetime.fromisoformat(os.getenv("AS_OF")).replace(tzinfo=IST)
    force = os.getenv("FORCE_RUN") == "1"
    if not force and (now.weekday() >= 5 or hm(now) < "09:30" or hm(now) > "15:40"):
        print(f"{now:%a %H:%M} - market time nahi hai, kuch nahi kiya", flush=True)
        return

    meta = load_json(META_F, {})
    if meta.get("date") != now.strftime("%Y-%m-%d"):
        meta = {"date": now.strftime("%Y-%m-%d"), "trades": 0, "stocks_today": [], "summary_sent": False}
    positions = load_json(POS_F, [])

    master = A.load_master()
    contracts = A.option_contracts(master)
    universe = A.fno_universe(master)
    if int(os.getenv("MAX_SYMBOLS", "0") or 0) > 0:
        universe = universe.head(int(os.getenv("MAX_SYMBOLS")))
    print(f"{now:%Y-%m-%d %H:%M} | F&O stocks {len(universe)} | open {len(positions)}", flush=True)

    closed_now = []
    positions = manage_open(positions, now, closed_now)

    if hm(now) < C.ENTRY_UPTO and len(positions) < C.MAX_OPEN and meta["trades"] < C.MAX_TRADES_PER_DAY:
        for cand in scan(now, universe, contracts, positions, meta):
            pos = try_enter(cand, now, contracts, positions, meta)
            if pos:
                positions.append(pos)

    # din khatam -> summary
    if hm(now) >= C.EXIT_AT and not positions and not meta.get("summary_sent"):
        t = pd.read_csv(TRADES_F) if TRADES_F.exists() else pd.DataFrame(columns=TRADE_COLS)
        t = t[t["date"] == meta["date"]]
        if len(t):
            send(f"📊 <b>Aaj ka paper result</b> ({meta['date']})\nTrades {len(t)} | "
                 f"Win {(t['pnl'] > 0).sum()} | Loss {(t['pnl'] <= 0).sum()}\n"
                 f"Gross: {inr(t['gross'].sum())} | Kharcha: -Rs {(t['slippage'] + t['charges']).sum():,.0f}\n"
                 f"Net P&L: <b>{inr(t['pnl'].sum())}</b>")
        else:
            send(f"📊 Aaj ({meta['date']}) koi option signal nahi aaya.")
        meta["summary_sent"] = True

    POS_F.write_text(json.dumps(positions, indent=1, default=str))
    META_F.write_text(json.dumps(meta, indent=1))
    write_status(positions, now)
    RUN.update(time=f"{now:%Y-%m-%d %H:%M}", fno=len(universe), open=len(positions),
               trades_today=meta["trades"], angel=dict(A.STATS))
    (STATE / "last_run.md").write_text("# Last run\n\n" + "\n".join(f"- {k}: {v}" for k, v in RUN.items()) + "\n")
    print("RUN", RUN, flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
