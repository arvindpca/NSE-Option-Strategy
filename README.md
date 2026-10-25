# NSE F&O Stock Option Strategy (Option BUYING) — Telegram + Paper Trading

> ⚠️ Ye bot **koi asli order nahi lagata**. Sirf Telegram alert + paper (kaagaz pe) trade log.
> Pehle 3-4 hafte paper result dekho, tabhi asli paisa lagane ka socho. Koi bhi strategy profit ki guarantee nahi deti.

## Strategy ek line me
Subah ke pehle 30 min (9:15–9:45) ka high/low banta hai. Uske baad koi F&O stock **zor ke volume ke saath** us range ke bahar nikle aur Nifty bhi usi taraf ho → us stock ka **ATM option** kharido (upar = CALL, neeche = PUT).

## Entry rules (15-min chart)
| | CALL (CE) kharido | PUT (PE) kharido |
|---|---|---|
| Breakout | Candle close > 9:15–9:45 ka HIGH | Candle close < 9:15–9:45 ka LOW |
| VWAP | Close > VWAP | Close < VWAP |
| Trend | EMA 9 > EMA 21 | EMA 9 < EMA 21 |
| Volume | > 1.5x pichhli 20 candle ka average | same |
| Candle | Green, body ≥ 50% | Red, body ≥ 50% |
| RSI(14) | 55 – 80 | 20 – 45 |
| Nifty | Nifty day-open aur EMA21 ke UPAR | Nifty dono ke NEECHE |
| Strength | Stock ka % change > Nifty ka | Stock ka % change < Nifty ka |
| Time | 9:45 – 1:30 PM | same |

Skip: gap > 3%, stock price 100–6000 ke bahar, option illiquid, 1 lot > Rs 30,000.

**Option:** ATM strike, mahine ki expiry (expiry 5 din se kam bachi ho to agle mahine ki).

## Exit rules (option premium pe)
- **Stop Loss:** premium −25%
- **Target:** premium +50%
- **No-loss:** premium +25% ho jaye → SL entry price pe
- **Breakout fail:** stock wapas range ke andar 15-min close kare → exit
- **3:15 PM** → sab band

**Risk:** 1 lot per trade, max 3 position ek saath, max 4 trade per din, ek stock me din me ek hi trade.
Sab settings: `option_bot/config.py`

## Setup (ek baar)
1. Repo → **Settings → Secrets and variables → Actions** me ye 6 secret daalo:
   `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD` (PIN), `ANGEL_TOTP_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
2. **Actions** tab → workflows enable karo.

## Kya chalta hai
| Workflow | Kab | Kya karta hai |
|---|---|---|
| **Option Paper Trader (live)** | Market time har 15 min (auto) | Signal → Telegram alert → paper entry/exit → `state/` me save |
| **Option Backtest (approx)** | Aap haath se chalao (Run workflow) | Pichhle 120 din ka backtest → `backtest/results/LATEST_REPORT.txt` |

Results dekhne ke liye: `state/STATUS.md` (live paper) aur `backtest/results/LATEST_REPORT.txt` (backtest).

## Backtest ke baare me sach
Angel One purane (expire ho chuke) options ka data nahi deta. Isliye backtest me option premium **formula (Black-Scholes) se andaaza** lagaya jata hai. Asli market me IV upar-neeche hota hai aur bid-ask spread hota hai, to asli result alag aayega. Backtest sirf ye batata hai ki signal me dum hai ya nahi — asli faisla **paper trading** ke result se karo.

## Files
```
option_bot/config.py         saare rules / numbers
option_bot/strategy.py       signal logic (backtest + live dono)
option_bot/exits.py          SL / target / no-loss logic
option_bot/option_picker.py  expiry + strike chunna
option_bot/paper_trader.py   live paper bot
option_bot/angel_api.py      Angel One login + data
backtest/backtest.py         backtest
tests/test_offline.py        bina login ke test (nakli data)
state/                       paper trades ka record
```
