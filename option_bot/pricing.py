"""
Backtest ke liye option premium ka ANDAAZA (Black-Scholes).
Purane (expire ho chuke) option ka data Angel One nahi deta, isliye premium formula se nikalte hain.
Ye asli premium jaisa nahi hoga - result ko "approx" hi samjho.
"""
import calendar
import math
from datetime import date, datetime, time, timedelta

RISK_FREE = 0.065


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, years, iv, side):
    years = max(years, 1 / (365 * 24))
    if iv <= 0:
        iv = 0.01
    d1 = (math.log(spot / strike) + (RISK_FREE + iv * iv / 2) * years) / (iv * math.sqrt(years))
    d2 = d1 - iv * math.sqrt(years)
    if side == "CE":
        return spot * _ncdf(d1) - strike * math.exp(-RISK_FREE * years) * _ncdf(d2)
    return strike * math.exp(-RISK_FREE * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def last_weekday(y, m, wd):
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() != wd:
        d -= timedelta(days=1)
    return d


def monthly_expiry(y, m):
    """NSE stock options: Sep 2025 se mahine ka aakhri Tuesday (pehle Thursday)."""
    return last_weekday(y, m, 1 if (y, m) >= (2025, 9) else 3)


def expiry_for(d: date, min_days: int) -> date:
    e = monthly_expiry(d.year, d.month)
    if (e - d).days < min_days:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        e = monthly_expiry(y, m)
    return e


def years_left(now: datetime, expiry: date) -> float:
    end = datetime.combine(expiry, time(15, 30), tzinfo=now.tzinfo)
    return max((end - now).total_seconds(), 600) / (365 * 24 * 3600)


def strike_step(spot: float) -> float:
    """NSE jaisa approx strike gap."""
    for lim, step in [(100, 2.5), (250, 5), (500, 10), (1000, 20), (2500, 50), (5000, 100)]:
        if spot <= lim:
            return step
    return 250
