"""Signal aaya -> kaunsa option contract kharidna hai (expiry + strike) tay karo."""
from datetime import date

import pandas as pd

from . import config as C


def pick_expiry(expiries, today: date):
    """Sabse paas wali expiry jisme kam se kam MIN_DAYS_TO_EXPIRY din bache hon."""
    for e in sorted(set(expiries)):
        if (e - today).days >= C.MIN_DAYS_TO_EXPIRY:
            return e
    return None


def pick_contract(contracts: pd.DataFrame, name: str, spot: float, side: str, today: date):
    """side = 'CE' / 'PE'.  Return: contract row (Series) ya None."""
    c = contracts[(contracts["name"] == name) & (contracts["opt_type"] == side)]
    if c.empty:
        return None
    exp = pick_expiry(c["expiry_d"], today)
    if exp is None:
        return None
    c = c[c["expiry_d"] == exp]
    strikes = sorted(c["strike_rs"].unique())
    atm = min(strikes, key=lambda k: abs(k - spot))
    k = atm
    if C.STRIKE_MODE == "ITM1":
        i = strikes.index(atm)
        if side == "CE" and i > 0:
            k = strikes[i - 1]          # CE ka ITM = neeche wala strike
        elif side == "PE" and i < len(strikes) - 1:
            k = strikes[i + 1]          # PE ka ITM = upar wala strike
    row = c[c["strike_rs"] == k].iloc[0]
    return row
