"""Exit rules (premium pe) - live aur backtest dono yahi use karte hain."""
from . import config as C


def new_levels(entry: float):
    return {"sl": round(entry * (1 - C.SL_PCT), 2), "target": round(entry * (1 + C.TARGET_PCT), 2)}


def step(pos: dict, o: float, h: float, l: float):
    """
    Ek option candle (open/high/low) pe check.  pos me 'entry','sl','target' hone chahiye.
    Return (reason, exit_price) ya None.  pos['sl'] breakeven pe update ho sakta hai.
    Conservative: ek hi candle me SL aur target dono chhue -> SL maana jayega.
    """
    sl, tgt = pos["sl"], pos["target"]
    if o <= sl:
        return ("SL", o)                       # gap ke saath SL ke neeche khula
    if o >= tgt:
        return ("TARGET", o)
    if l <= sl:
        return ("SL" if sl < pos["entry"] else "BREAKEVEN", sl)
    if h >= tgt:
        return ("TARGET", tgt)
    if h >= pos["entry"] * (1 + C.BREAKEVEN_AT) and pos["sl"] < pos["entry"]:
        pos["sl"] = pos["entry"]              # ab no-loss
    return None


def charges(buy_val: float, sell_val: float) -> float:
    """Angel One option charges (Oct 2026): brokerage Rs 20/order, STT 0.15% sell, exch 0.0355%,
    SEBI Rs 10/cr, stamp 0.003% buy, GST 18%."""
    if not getattr(C, "REAL_CHARGES", False):
        return C.COST_PER_TRADE
    turn = buy_val + sell_val
    brok, exch, sebi = 40.0, 0.000355299 * turn, turn * 1e-6
    return brok + exch + sebi + 0.0015 * sell_val + 0.00003 * buy_val + 0.18 * (brok + exch + sebi)


def pnl(entry: float, exit_price: float, qty: int) -> float:
    """Slippage + charges ke baad Rs profit."""
    buy = entry * (1 + C.SLIPPAGE_PCT)
    sell = exit_price * (1 - C.SLIPPAGE_PCT)
    return round((sell - buy) * qty - charges(buy * qty, sell * qty), 2)


def breakdown(entry: float, exit_price: float, qty: int) -> dict:
    """Gross (bina kharche) / slippage / charges / net - sab Rs me."""
    gross = (exit_price - entry) * qty
    buy, sell = entry * (1 + C.SLIPPAGE_PCT), exit_price * (1 - C.SLIPPAGE_PCT)
    slip = gross - (sell - buy) * qty
    ch = charges(buy * qty, sell * qty)
    return {"gross": round(gross, 2), "slippage": round(slip, 2), "charges": round(ch, 2),
            "pnl": round(gross - slip - ch, 2)}
