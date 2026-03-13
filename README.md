# ConsiderTrader Dashboard

Real-time BTC/ETH analysis system.
By combining Volatility + Wallet Flow + Sentiment + On-Chain data,
it enables you to make **data-driven futures trade decisions**.

---

## Quick Start

```bash
# 1. Install dependencies
pip install flask requests numpy

# 2. Run
python app.py

# 3. Open in browser
http://localhost:5005
```

---

## Backtest

```bash
python backtest.py              # BTC — 90 days
python backtest.py ETH 180      # ETH — 180 days
python backtest.py BTC 365      # BTC — 1 year
```

Results are printed to the terminal and automatically saved as a JSON file.

---

## Project Structure

```
crypto_dashboard/
├── app.py                   → Analysis engine + Flask web server
├── backtest.py              → Historical data test module
├── templates/
│   └── dashboard.html       → Web interface
├── requirements.txt         → Dependencies
└── README.md
```

---

## Data Sources

All free, no API key required.

| Source | Data | URL |
|--------|------|-----|
| Binance Public API | Price, Volume, Klines (1H + 4H) | api.binance.com |
| Binance Futures API | Funding Rate, Long/Short Ratio, Open Interest | fapi.binance.com |
| Alternative.me | Fear & Greed Index | api.alternative.me |
| DefiLlama | ETH Stablecoin Flow (wallet inflow/outflow proxy) | stablecoins.llama.fi |

---

## Dashboard Panels

### ◈ Live Price
- Real-time BTC/ETH price
- 24H change, High/Low, Volume
- ATR (volatility unit, used in stop calculation)

### ◈ Combined Signal
Evaluates 5 conditions. Generates a signal when 4/5 are met:

| Condition | Criteria |
|-------|--------|
| Volatility normal | Vol score < 120 |
| Flow positive | Wallet Flow > 30 |
| Funding healthy | Between -0.05% and +0.05% |
| L/S balanced | Long ratio 40%–65% |
| F&G healthy | Fear & Greed > 30 |

### ◈ 4H Trend Filter
EMA20 vs EMA50 comparison.
- Trend UP → Long signals are valid
- Trend DOWN → All signals are blocked (counter-trend = gambling)

### ◈ Volatility Score
ATR normalized + Bollinger Band width combination.
- 100 = historical average
- \> 140 = high volatility, caution
- < 80 = low volatility, opportunity zone

### ◈ Wallet Flow
- **OBV** → Shows whether large players are accumulating or distributing
- **CVD Delta** → Instant buying/selling pressure inside each candle
- **DefiLlama ETH** → Stablecoin inflows/outflows into the Ethereum chain

### ◈ Market Sentiment
- Fear & Greed Index (0–100)
- Global Long/Short ratio
- Funding Rate interpretation

### ◈ Risk Management
Automatically calculated upon each update:
- Entry price
- Stop Loss: ATR × 1.5
- Take Profit: ATR × 3.0
- R/R Ratio: 1:2

---

## Strategy Parameters

| Parameter | Value |
|-----------|-------|
| Asset | BTC/USDT + ETH/USDT |
| Timeframe | 1H entry + 4H trend filter |
| Leverage | 5x |
| Risk / Trade | 2% |
| Stop | ATR × 1.5 |
| Target | ATR × 3.0 |
| Risk/Reward | 1 : 2 |
| Min Condition | 4 out of 5 |

---

## How Backtest Works?

```
Historical 1H data is fetched from Binance
        ↓
Indicators are calculated for each candle
        ↓
When 4 out of 5 conditions are met, it counts as an "entry"
        ↓
Exits when Stop (ATR×1.5) or TP (ATR×3) is triggered
        ↓
Statistics are calculated (winrate, R/R, drawdown, Sharpe)
```

**Important:** Backtest shows historical performance, does not guarantee future results.

---

## Roadmap

```
✅ Currently ready
   • BTC/ETH dashboard
   • Wallet Flow (OBV + CVD)
   • 4H trend filter
   • Funding Rate analysis
   • DefiLlama stablecoin flow
   • Auto Stop/TP calculation
   • Backtest module

🔜 Next steps
   • Telegram/Discord alert (notification on signal)
   • WebSocket (real-time instead of 30s polling)
   • Trade journal (SQLite — transaction logbook)
   • DefiLlama expansion (TVL, DEX volumes)
```

---

## Common Errors

| Error | Solution |
|------|-------|
| `ModuleNotFoundError` | `pip install flask requests numpy` |
| `Port already in use` | `python app.py` → if port 5005 is in use, try another |
| `Connection error` | Check internet connection |
| No data | Binance might require a VPN in some countries |

---

## Architecture

```
Binance API    ──┐
Futures API    ──┤                        ┌── /api/data
Alternative.me ──┼──▶ app.py (Python) ───┤── /api/btc
DefiLlama      ──┘         │             └── /api/eth
                        Cache (30s)
                             │
                       dashboard.html
                       (Browser)
```
