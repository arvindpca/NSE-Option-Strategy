"""Option volume unit check: 5-min candle volume (sum) vs marketData tradeVolume. Koi trade nahi."""
import os, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from option_bot import angel_api as A
from option_bot.option_picker import pick_contract
IST = A.IST
now = datetime.now(IST).replace(second=0, microsecond=0)
master = A.load_master(); con = A.option_contracts(master)
out = []
for stock, tok, side in [("RELIANCE", "2885", "PE"), ("RELIANCE", "2885", "CE"), ("SBIN", "3045", "CE")]:
    spot = A.ltp("NSE", f"{stock}-EQ", tok)
    c = pick_contract(con, stock, spot, side, now.date())
    oc = A.candles(c["token"], "NFO", "FIVE_MINUTE", now.replace(hour=9, minute=15), now)
    csum = float(oc["volume"].sum()) if len(oc) else 0
    md = A.client().getMarketData("FULL", {"NFO": [str(c["token"])]})
    f = ((md or {}).get("data") or {}).get("fetched") or [{}]
    tv = f[0].get("tradeVolume")
    out.append(f"{c['symbol']} lot={c['lot']} candle_sum={csum:.0f} tradeVolume={tv} ratio={(tv or 0)/csum if csum else 'na'}")
Path("state/vol_check.txt").write_text("\n".join(out) + "\n"); print("\n".join(out))
