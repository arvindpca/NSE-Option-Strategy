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


def pnl(entry: float, exit_price: float, qty: int) -> float:
    """Slippage + charges ke baad Rs profit."""
    buy = entry * (1 + C.SLIPPAGE_PCT)
    sell = exit_price * (1 - C.SLIPPAGE_PCT)
    return round((sell - buy) * qty - C.COST_PER_TRADE, 2)
