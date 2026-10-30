#!/usr/bin/env python3
"""
Offline test (bina Angel login ke): nakli data bana ke
  1) signal logic, 2) exit logic, 3) backtest, 4) live paper trader (mock API) chalate hain.
Run:  python tests/test_offline.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
IST = ZoneInfo("Asia/Kolkata")


def fake_day(d, base, trend, rng, vol=1e5):
    """Ek din ki 25 candles (15m). trend>0 -> 9:45 ke baad upar breakout."""
    rows, px = [], base
    for k in range(25):
        t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST) + timedelta(minutes=15 * k)
        if k < 2:
            o, c = px, px + rng.normal(0, base * 0.001)
        elif k == 2 and trend:
            o, c = px, px + trend * base * 0.012     # majboot breakout candle
        else:
            o, c = px, px + trend * base * 0.0015 + rng.normal(0, base * 0.0008)
        h, l = max(o, c) + base * 0.0005, min(o, c) - base * 0.0005
        v = vol * (4 if (k == 2 and trend) else 1) * rng.uniform(0.8, 1.2)
        rows.append([t, o, h, l, c, v])
        px = c
    return rows


def fake_series(days, base, trends, seed):
    rng = np.random.default_rng(seed)
    rows, px = [], base
    for d, tr in zip(days, trends):
        day = fake_day(d, px, tr, rng)
        rows += day
        px = day[-1][4]
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


def trading_days(n, end):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def test_exits():
    from option_bot import exits as X
    from option_bot import config as C
    saved = (C.BREAKEVEN_AT, C.TARGET_PCT)
    C.BREAKEVEN_AT, C.TARGET_PCT = 0.25, 0.50          # exit logic ka test purane numbers se
    pos = {"entry": 100, **X.new_levels(100)}
    assert X.step(pos, 100, 110, 95) is None
    assert X.step(pos, 100, 126, 99) is None and pos["sl"] == 100          # breakeven chalu
    assert X.step(pos, 120, 121, 99) == ("BREAKEVEN", 100)
    pos = {"entry": 100, **X.new_levels(100)}
    assert X.step(pos, 100, 151, 74) == ("SL", 75)                         # dono chhue -> SL
    assert X.step({"entry": 100, **X.new_levels(100)}, 100, 152, 90) == ("TARGET", 150)
    C.BREAKEVEN_AT, C.TARGET_PCT = 9.0, 1.00
    pos = {"entry": 100, **X.new_levels(100)}
    assert X.step(pos, 100, 160, 80) is None and pos["sl"] == 75            # v3: breakeven band
    assert X.step(pos, 150, 201, 140) == ("TARGET", 200)
    C.BREAKEVEN_AT, C.TARGET_PCT = saved
    print("exits OK")


def test_signals_and_backtest(tmp):
    import backtest.backtest as BT
    from option_bot import strategy as S

    days = trading_days(45, datetime(2026, 10, 23).date())
    rng = np.random.default_rng(1)
    nifty_tr = [[1, 1, 0, -1, -1, -1, 0][i % 7] for i in range(len(days))]
    nifty = fake_series(days, 25000, nifty_tr, 7)
    data = Path(tmp) / "data"
    data.mkdir()
    nifty.to_csv(data / "NIFTY.csv", index=False)
    for s, base in [("AAA", 1500), ("BBB", 800), ("CCC", 2400)]:
        tr = [x * 1.3 if rng.random() > 0.3 else 0 for x in nifty_tr]
        fake_series(days, base, tr, sum(map(ord, s))).to_csv(data / f"{s}.csv", index=False)
    pd.DataFrame({"name": ["AAA", "BBB", "CCC"], "lot": [250, 500, 150]}).to_csv(data / "_lots.csv", index=False)

    sig = S.add_signals(BT.read_csv(data / "AAA.csv"), BT.read_csv(data / "NIFTY.csv"))
    n_ce, n_pe = (sig["signal"] == "CE").sum(), (sig["signal"] == "PE").sum()
    print(f"AAA signals: CE {n_ce}, PE {n_pe}")
    assert n_ce > 0 and n_pe > 0
    assert (sig.loc[sig["signal"] != "", "hhmm"] >= "09:45").all()

    BT.DATA, BT.OUT = data, Path(tmp) / "results"
    os.environ["OFFLINE"] = "1"
    BT.run()
    assert (Path(tmp) / "results" / "LATEST_REPORT.txt").exists()
    print("backtest OK")
    return days, data


def test_live_paper(tmp, data):
    """Mock Angel API se ek din ka live paper run (har 15 min)."""
    import backtest.backtest as BT
    from option_bot import angel_api as A
    from option_bot import pricing as Pr

    store = {p.stem: BT.read_csv(p) for p in data.glob("*.csv") if p.stem != "_lots"}
    tok = {"99926000": "NIFTY", "1": "AAA", "2": "BBB", "3": "CCC"}
    # sabse aakhri din jisme AAA pe CE signal hai
    from option_bot import strategy as S
    sig = S.add_signals(store["AAA"], store["NIFTY"])
    day = sig[sig["signal"] == "CE"]["date"].iloc[-1]
    # option contracts (mahine ki expiry, ATM ke aas paas strikes)
    rows = []
    exp = Pr.expiry_for(day, 5)
    for name, tkn, lot in [("AAA", "1", 250), ("BBB", "2", 500), ("CCC", "3", 150)]:
        spot = store[name]["close"].iloc[-1]
        step = Pr.strike_step(spot)
        for j in range(-10, 11):
            k = round(spot / step) * step + j * step
            for typ in ("CE", "PE"):
                rows.append({"name": name, "symbol": f"{name}{exp:%d%b%y}{k:g}{typ}".upper(), "token": f"O{name}{k}{typ}",
                             "expiry_d": exp, "strike_rs": k, "lot": lot, "opt_type": typ})
    contracts = pd.DataFrame(rows)
    opt = contracts.set_index("token").to_dict("index")

    def fake_candles(token, exchange, interval, start, end):
        if exchange == "NFO":            # option candles: BS se underlying ke 5m se
            o = opt[token]
            u = store[o["name"]]
            u = u[(u["time"] >= pd.Timestamp(start)) & (u["time"] <= pd.Timestamp(end))]
            out = []
            for _, c in u.iterrows():
                for q in range(3):
                    t = c["time"] + pd.Timedelta(minutes=5 * q)
                    f = lambda s: Pr.bs_price(s, o["strike_rs"], Pr.years_left(t.to_pydatetime(), o["expiry_d"]), 0.3, o["opt_type"])  # noqa: E731
                    a, b = f(c["high"]), f(c["low"])
                    out.append([t, f(c["open"]), max(a, b), min(a, b), f(c["close"]), 5000])
            return pd.DataFrame(out, columns=["time", "open", "high", "low", "close", "volume"])
        df = store[tok[token]]
        return df[(df["time"] >= pd.Timestamp(start)) & (df["time"] <= pd.Timestamp(end))].reset_index(drop=True)

    A.load_master = lambda: pd.DataFrame()
    A.option_contracts = lambda m: contracts
    A.fno_universe = lambda m: pd.DataFrame({"name": ["AAA", "BBB", "CCC"], "token": ["1", "2", "3"]})
    A.candles = fake_candles
    A.ltp = lambda ex, sym, tkn: None   # LTP fail -> candle close fallback test
    A.quotes = lambda toks, exchange="NSE": None   # batch quote fail -> sab stocks scan (fallback test)

    import option_bot.paper_trader as PT
    from option_bot import config as C
    C.MAX_IV = 0.99                      # fake data bahut uchhalta hai -> IV filter test ke liye dheela
    # prefilter test: fake quotes -> sirf upar wale stock CE ke liye bache
    A.quotes = lambda toks, exchange="NSE": {"1": dict(ltp=110, open=100, high=111, low=99, close=100, volume=1),
                                             "2": dict(ltp=90, open=100, high=101, low=89, close=100, volume=1)}
    PT.RUN.clear()
    u = pd.DataFrame({"name": ["AAA", "BBB", "CCC"], "token": ["1", "2", "3"]})
    assert list(PT.prefilter(u, {"CE"}, 0.2)["name"]) == ["AAA", "CCC"]      # CCC ka quote nahi -> safe, scan
    assert list(PT.prefilter(u, {"PE"}, 0.2)["name"]) == ["BBB", "CCC"]
    A.quotes = lambda toks, exchange="NSE": None   # batch quote fail -> sab stocks scan (fallback)
    sent = []
    PT.send = lambda txt: sent.append(txt) or print("  [TG]", txt.replace("\n", " | "))
    state = Path(tmp) / "state"
    PT.STATE = state
    PT.POS_F, PT.TRADES_F, PT.SIG_F = state / "positions.json", state / "trades.csv", state / "signals_log.csv"
    PT.META_F, PT.STATUS_F = state / "meta.json", state / "STATUS.md"
    t = datetime(day.year, day.month, day.day, 9, 32)
    while t.strftime("%H:%M") <= "15:32":
        os.environ["AS_OF"] = t.isoformat()
        PT.main()
        t += timedelta(minutes=15)
    assert any("PAPER" in s and "BUY" in s for s in sent), "entry alert nahi aaya"
    assert any("PAPER EXIT" in s for s in sent), "exit alert nahi aaya"
    assert (state / "trades.csv").exists() and (state / "STATUS.md").exists()
    print((state / "STATUS.md").read_text())
    print("live paper OK")


if __name__ == "__main__":
    test_exits()
    with tempfile.TemporaryDirectory() as tmp:
        days, data = test_signals_and_backtest(tmp)
        test_live_paper(tmp, data)
    print("\nSAB TEST PASS")
