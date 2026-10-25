"""
STRATEGY RULES (v1) - sab settings yahan hain
=============================================
Naam : F&O Stock Momentum Breakout - Option BUYING (CE / PE)
Mode : Telegram alert + Paper trading (koi asli order NAHI lagta)

Simple bhasha me:
  1. Subah 9:15-9:45 ka high/low = "Opening Range" (OR)
  2. 9:45 ke baad koi F&O stock 15-min candle me OR high ke UPAR band ho
     + VWAP ke upar + EMA9 > EMA21 + volume 1.5x se zyada + Nifty bhi upar
     -> us stock ka ATM CALL (CE) kharido
  3. Ulta (OR low ke NEECHE, sab cheez neeche)  -> ATM PUT (PE) kharido
  4. Exit: premium -25% = Stop Loss | premium +50% = Target
           premium +25% ho gaya -> SL entry pe (no-loss)
           stock wapas OR ke andar 15-min close kare -> exit
           3:15 PM -> sab band
"""

# ---------------- time (IST) ----------------
OR_START = "09:15"          # opening range start
OR_END = "09:45"            # opening range khatam (pehli 2 candle 15m)
ENTRY_FROM = "09:45"        # isse pehle entry nahi
ENTRY_UPTO = "13:30"        # iske baad naya trade nahi (theta / samay kam)
EXIT_AT = "15:15"           # sab position band

# ---------------- stock filter ----------------
MIN_PRICE = 100
MAX_PRICE = 6000
MAX_GAP_PCT = 3.0           # aaj ka gap 3% se zyada -> skip (already move ho chuka)

# ---------------- signal ----------------
EMA_FAST = 9
EMA_SLOW = 21
VOL_MULT = 1.5              # breakout candle volume > 1.5 x pichhli 20 candle ka average
VOL_LOOKBACK = 20
MIN_BODY_PCT = 0.5          # candle body >= 50% of range (majboot candle)
RSI_LEN = 14
RSI_CE_MIN, RSI_CE_MAX = 55, 80
RSI_PE_MIN, RSI_PE_MAX = 20, 45
USE_NIFTY_FILTER = True     # CE sirf jab Nifty upar, PE sirf jab Nifty neeche
SIGNAL_LOOKBACK_CANDLES = 2 # GitHub run late ho to pichhli 2 candle tak ka fresh breakout maan lo

# ---------------- option choose ----------------
STRIKE_MODE = "ATM"         # "ATM" ya "ITM1" (1 strike in-the-money)
MIN_DAYS_TO_EXPIRY = 5      # expiry 5 din se kam -> agle mahine ka contract
MIN_PREMIUM = 5.0           # isse sasta option skip (illiquid)
MIN_OPTION_VOL_LOTS = 20    # aaj ka option volume kam se kam 20 lot

# ---------------- money / risk ----------------
MAX_COST_PER_TRADE = 30_000 # 1 lot ki keemat isse zyada -> skip
LOTS_PER_TRADE = 1
MAX_OPEN = 3                # ek time pe max 3 position
MAX_TRADES_PER_DAY = 4

SL_PCT = 0.25               # premium 25% gira -> exit
TARGET_PCT = 0.50           # premium 50% badha -> exit
BREAKEVEN_AT = 0.25         # premium +25% -> SL ko entry pe le aao
COST_PER_TRADE = 60.0       # brokerage + charges (round trip, approx Rs)
SLIPPAGE_PCT = 0.01         # 1% premium slippage (paper me realistic rehne ke liye)

NIFTY_TOKEN = "99926000"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
