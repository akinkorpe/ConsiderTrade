"""
ConsiderTrader Backtest Module
────────────────────────────
Usage:
  python backtest.py            → BTC 90 days
  python backtest.py ETH 180    → ETH 180 days

Strategy:
  Entry   : When 4 out of 5 conditions are met
  Stop    : ATR x 1.5
  Target  : ATR x 3.0  (1:2 risk/reward)
  Risk    : 2% of capital per trade
  Leverage: 5x
"""

import requests
import numpy as np
import sys
import json
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
CONFIG = {
    "risk_per_trade": 0.02,    # 2%
    "leverage":       5,
    "atr_stop_mult":  1.5,
    "atr_tp_mult":    3.0,
    "min_conditions": 4,       # how many out of 5 conditions to meet
    "capital":        10000,   # initial capital ($)
    "fee":            0.0004,  # 0.04% taker fee (Binance Futures)
}

# ─────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────

def fetch_klines(symbol="BTCUSDT", days=90):
    """Fetches historical candle data from Binance"""
    print(f"📡 chunking {days} days of data for {symbol}...")
    
    end_ms   = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    
    all_klines = []
    current    = start_ms
    
    while current < end_ms:
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                "symbol":    symbol,
                "interval":  "1h",
                "startTime": current,
                "endTime":   end_ms,
                "limit":     1000
            }
            r    = requests.get(url, params=params, timeout=15)
            data = r.json()
            
            if not data or isinstance(data, dict):
                break
                
            all_klines.extend(data)
            current = data[-1][0] + 3600000  # +1 hour
            
            if len(data) < 1000:
                break
                
        except Exception as e:
            print(f"Data fetch error: {e}")
            break
    
    print(f"✅ {len(all_klines)} candle data received")
    
    return {
        "times":   [k[0] for k in all_klines],
        "opens":   [float(k[1]) for k in all_klines],
        "highs":   [float(k[2]) for k in all_klines],
        "lows":    [float(k[3]) for k in all_klines],
        "closes":  [float(k[4]) for k in all_klines],
        "volumes": [float(k[5]) for k in all_klines],
    }

# ─────────────────────────────────────────────
# INDICATOR CALCULATIONS
# ─────────────────────────────────────────────

def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    
    atrs = [np.mean(trs[:period])]
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period-1) + trs[i]) / period)
    
    # Normalize (100 = average)
    norm_atrs = []
    for i in range(len(atrs)):
        window    = atrs[max(0, i-42):i+1]
        baseline  = np.mean(window) if window else atrs[i]
        norm_atrs.append((atrs[i] / baseline) * 100 if baseline > 0 else 100)
    
    return atrs, norm_atrs

def calc_obv_norm(closes, volumes, period=10):
    obv = 0
    obvs = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        obvs.append(obv)
    
    # Smooth + normalize
    result = []
    for i in range(len(obvs)):
        if i < period * 2:
            result.append(0)
            continue
        smoothed  = np.mean(obvs[i-period:i])
        baseline  = np.mean(obvs[i-period*4:i])
        norm      = (smoothed - baseline) / (abs(baseline) + 1) * 100
        result.append(norm)
    
    return result

def calc_cvd_norm(closes, highs, lows, volumes, period=20):
    deltas = []
    for i in range(len(closes)):
        rng = highs[i] - lows[i]
        if rng == 0:
            deltas.append(0)
            continue
        buy_vol = ((closes[i] - lows[i]) / rng) * volumes[i]
        deltas.append(buy_vol - (volumes[i] - buy_vol))
    
    cvd    = np.cumsum(deltas)
    result = []
    for i in range(len(cvd)):
        if i < period * 3:
            result.append(0)
            continue
        window   = cvd[i-period*3:i]
        baseline = np.mean(window)
        std      = np.std(window) + 1
        result.append((cvd[i] - baseline) / std * 50)
    
    return result

def calc_bb_width_norm(closes, period=20):
    result = []
    for i in range(len(closes)):
        if i < period * 2:
            result.append(100)
            continue
        recent   = closes[i-period:i]
        mid      = np.mean(recent)
        std      = np.std(recent)
        width    = (4 * std) / (mid + 1) * 100
        
        all_w = []
        for j in range(period, i):
            s = closes[j-period:j]
            m = np.mean(s)
            sd = np.std(s)
            all_w.append((4 * sd) / (m + 1) * 100)
        
        baseline = np.mean(all_w) if all_w else width
        result.append((width / baseline) * 100 if baseline > 0 else 100)
    
    return result

# ─────────────────────────────────────────────
# SIGNAL GENERATOR
# ─────────────────────────────────────────────

def generate_signals(data):
    """Calculates signals for each candle"""
    closes  = data["closes"]
    highs   = data["highs"]
    lows    = data["lows"]
    volumes = data["volumes"]
    times   = data["times"]
    n       = len(closes)
    
    print("⚙️  Calculating indicators...")
    
    atrs_raw, atrs_norm = calc_atr(highs, lows, closes)
    obv_norm            = calc_obv_norm(closes, volumes)
    cvd_norm            = calc_cvd_norm(closes, highs, lows, volumes)
    bb_norm             = calc_bb_width_norm(closes)
    
    # ATR offset (period diff)
    atr_offset = n - len(atrs_raw)
    
    signals = []
    
    for i in range(50, n):  # skip first 50 candles for warmup
        atr_idx = i - atr_offset
        if atr_idx < 0 or atr_idx >= len(atrs_raw):
            continue
        
        atr_val  = atrs_raw[atr_idx]
        atr_norm = atrs_norm[atr_idx]
        
        # Combined volatility score
        vol_score  = (atr_norm + bb_norm[i]) / 2
        
        # Flow score
        flow_score = obv_norm[i] * 0.4 + cvd_norm[i] * 0.6
        
        # 5 conditions (funding/L-S data isolated in history, assume neutral)
        cond1 = vol_score < 120           # Volatility normal
        cond2 = flow_score > 30           # Buying pressure
        cond3 = True                      # Funding (no historical data, assume neutral)
        cond4 = True                      # L/S ratio (no historical data, assume neutral)
        cond5 = vol_score < 100           # Low volatility bonus

        score = sum([cond1, cond2, cond3, cond4, cond5])
        
        if score >= CONFIG["min_conditions"]:
            signals.append({
                "idx":        i,
                "time":       times[i],
                "price":      closes[i],
                "atr":        atr_val,
                "vol_score":  round(vol_score, 1),
                "flow_score": round(flow_score, 1),
                "conditions": score,
            })
    
    print(f"✅ {len(signals)} potential signals found")
    return signals

# ─────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────

def run_backtest(data, signals):
    """Simulates signals and returns results"""
    closes  = data["closes"]
    highs   = data["highs"]
    lows    = data["lows"]
    times   = data["times"]
    n       = len(closes)
    
    capital    = CONFIG["capital"]
    trades     = []
    equity     = [capital]
    used_bars  = set()  # No multiple trades at the same time
    
    print("🔄 Running backtest...")
    
    for sig in signals:
        i = sig["idx"]
        
        # Skip if there is already an open position
        if any(i in range(t["entry_bar"], t["exit_bar"]+1) for t in trades if "exit_bar" in t):
            continue
        
        entry_price = closes[i]
        atr         = sig["atr"]
        
        stop_dist   = atr * CONFIG["atr_stop_mult"]
        tp_dist     = atr * CONFIG["atr_tp_mult"]
        stop_price  = entry_price - stop_dist
        tp_price    = entry_price + tp_dist
        
        # Position size
        risk_amount  = capital * CONFIG["risk_per_trade"]
        position_usd = (risk_amount / stop_dist) * entry_price
        position_usd = min(position_usd, capital * CONFIG["leverage"])
        qty          = position_usd / entry_price
        
        # Fee
        entry_fee = position_usd * CONFIG["fee"]
        
        # Find result in next candles
        result     = None
        exit_bar   = i
        exit_price = entry_price
        bars_held  = 0
        
        for j in range(i+1, min(i+72, n)):  # Wait max 72 candles (3 days)
            bars_held += 1
            
            # Stop triggered?
            if lows[j] <= stop_price:
                result     = "STOP"
                exit_price = stop_price
                exit_bar   = j
                break
            
            # TP triggered?
            if highs[j] >= tp_price:
                result     = "TP"
                exit_price = tp_price
                exit_bar   = j
                break
        
        # Timeout
        if result is None:
            result     = "TIMEOUT"
            exit_price = closes[min(i+72, n-1)]
            exit_bar   = min(i+72, n-1)
            bars_held  = 72
        
        # Calculate PnL
        exit_fee    = (qty * exit_price) * CONFIG["fee"]
        gross_pnl   = (exit_price - entry_price) * qty
        net_pnl     = gross_pnl - entry_fee - exit_fee
        pnl_pct     = (net_pnl / capital) * 100
        capital    += net_pnl
        
        trades.append({
            "entry_time":  datetime.fromtimestamp(times[i]/1000).strftime("%Y-%m-%d %H:%M"),
            "exit_time":   datetime.fromtimestamp(times[exit_bar]/1000).strftime("%Y-%m-%d %H:%M"),
            "entry_price": round(entry_price, 2),
            "exit_price":  round(exit_price, 2),
            "stop":        round(stop_price, 2),
            "tp":          round(tp_price, 2),
            "result":      result,
            "pnl":         round(net_pnl, 2),
            "pnl_pct":     round(pnl_pct, 3),
            "capital":     round(capital, 2),
            "bars_held":   bars_held,
            "entry_bar":   i,
            "exit_bar":    exit_bar,
            "conditions":  sig["conditions"],
        })
        
        equity.append(capital)
    
    return trades, equity

# ─────────────────────────────────────────────
# STATISTICS
# ─────────────────────────────────────────────

def calc_stats(trades, equity, initial_capital):
    if not trades:
        return {}
    
    wins      = [t for t in trades if t["pnl"] > 0]
    losses    = [t for t in trades if t["pnl"] <= 0]
    tps       = [t for t in trades if t["result"] == "TP"]
    stops     = [t for t in trades if t["result"] == "STOP"]
    timeouts  = [t for t in trades if t["result"] == "TIMEOUT"]
    
    total        = len(trades)
    winrate      = len(wins) / total * 100
    final_cap    = trades[-1]["capital"]
    total_return = (final_cap - initial_capital) / initial_capital * 100
    
    avg_win  = np.mean([t["pnl"] for t in wins])  if wins   else 0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0
    rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for cap in equity:
        if cap > peak:
            peak = cap
        dd = (peak - cap) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    # Consecutive losses
    max_consec_loss = 0
    consec = 0
    for t in trades:
        if t["pnl"] <= 0:
            consec += 1
            max_consec_loss = max(max_consec_loss, consec)
        else:
            consec = 0
    
    # Sharpe (simple)
    pnl_series = [t["pnl_pct"] for t in trades]
    sharpe = (np.mean(pnl_series) / (np.std(pnl_series) + 0.001)) * np.sqrt(252) if pnl_series else 0
    
    return {
        "total_trades":       total,
        "wins":               len(wins),
        "losses":             len(losses),
        "winrate":            round(winrate, 1),
        "tp_hits":            len(tps),
        "stop_hits":          len(stops),
        "timeouts":           len(timeouts),
        "avg_win":            round(avg_win, 2),
        "avg_loss":           round(avg_loss, 2),
        "rr_ratio":           round(rr_ratio, 2),
        "total_return":       round(total_return, 2),
        "final_capital":      round(final_cap, 2),
        "max_drawdown":       round(max_dd, 2),
        "max_consec_losses":  max_consec_loss,
        "sharpe":             round(sharpe, 2),
        "avg_bars_held":      round(np.mean([t["bars_held"] for t in trades]), 1),
    }

# ─────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────

def print_report(stats, trades, symbol, days):
    s = stats
    
    print("\n" + "═"*52)
    print(f"  📊 BACKTEST REPORT — {symbol} / {days} DAYS")
    print("═"*52)
    
    print(f"\n  GENERAL")
    print(f"  {'Total Trades':<25} {s['total_trades']}")
    print(f"  {'Win / Loss':<25} {s['wins']} W / {s['losses']} L")
    print(f"  {'Winrate':<25} {s['winrate']}%")
    print(f"  {'TP Hit / Stop Hit':<25} {s['tp_hits']} / {s['stop_hits']}")
    print(f"  {'Timeout':<25} {s['timeouts']}")
    
    print(f"\n  PnL")
    print(f"  {'Total Return':<25} {s['total_return']}%")
    print(f"  {'Initial Capital':<25} ${CONFIG['capital']:,.0f}")
    print(f"  {'Final Capital':<25} ${s['final_capital']:,.2f}")
    print(f"  {'Avg. Win':<25} ${s['avg_win']:,.2f}")
    print(f"  {'Avg. Loss':<25} ${s['avg_loss']:,.2f}")
    print(f"  {'Risk/Reward Ratio':<25} {s['rr_ratio']}x")
    
    print(f"\n  RISK")
    print(f"  {'Max Drawdown':<25} {s['max_drawdown']}%")
    print(f"  {'Max Consec. Losses':<25} {s['max_consec_losses']}")
    print(f"  {'Sharpe Ratio':<25} {s['sharpe']}")
    print(f"  {'Avg. Position Duration':<25} {s['avg_bars_held']} candles")
    
    print(f"\n  LAST 5 TRADES")
    print(f"  {'Date':<18} {'Entry':>8} {'Exit':>8} {'Result':<8} {'PnL':>8}")
    print("  " + "─"*50)
    for t in trades[-5:]:
        pnl_str = f"+${t['pnl']:.0f}" if t['pnl'] > 0 else f"-${abs(t['pnl']):.0f}"
        print(f"  {t['entry_time']:<18} {t['entry_price']:>8.0f} {t['exit_price']:>8.0f} {t['result']:<8} {pnl_str:>8}")
    
    print("\n" + "═"*52)
    
    # Comment
    print("\n  💬 COMMENT")
    if s['winrate'] >= 50 and s['rr_ratio'] >= 1.5 and s['total_return'] > 0:
        print("  ✅ Strategy performed POSITIVE in this period.")
        print("     But past performance is not a guarantee for the future.")
    elif s['total_return'] > 0 and s['rr_ratio'] >= 2:
        print("  🟡 Winrate is low but R/R ratio is high.")
        print("     Could work in long run, test more data.")
    else:
        print("  🔴 Strategy underperformed in this period.")
        print("     Review parameters.")
    
    if s['max_drawdown'] > 20:
        print(f"\n  ⚠️  Max drawdown {s['max_drawdown']}% — Tighten risk management.")
    
    print()

def save_results(stats, trades, symbol, days):
    """Saves results as JSON"""
    output = {
        "meta": {
            "symbol":    symbol,
            "days":      days,
            "config":    CONFIG,
            "run_time":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "stats":  stats,
        "trades": trades,
    }
    fname = f"backtest_{symbol}_{days}d_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(fname, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  💾 Results saved: {fname}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    symbol = sys.argv[1].upper() + "USDT" if len(sys.argv) > 1 else "BTCUSDT"
    days   = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    
    print(f"\n🚀 ConsiderTrader Backtest — {symbol} / {days} days")
    print(f"   Leverage: {CONFIG['leverage']}x | Risk: {CONFIG['risk_per_trade']*100}% | R/R: 1:{CONFIG['atr_tp_mult']/CONFIG['atr_stop_mult']:.0f}\n")
    
    # 1. Fetch data
    data = fetch_klines(symbol, days)
    
    if len(data["closes"]) < 100:
        print("❌ Insufficient data. Check internet connection.")
        sys.exit(1)
    
    # 2. Generate signal
    signals = generate_signals(data)
    
    if not signals:
        print("❌ No signals could be generated. Loosen parameters.")
        sys.exit(1)
    
    # 3. Run backtest
    trades, equity = run_backtest(data, signals)
    
    if not trades:
        print("❌ No trades executed.")
        sys.exit(1)
    
    # 4. Calculate stats
    stats = calc_stats(trades, equity, CONFIG["capital"])
    
    # 5. Print report
    print_report(stats, trades, symbol, days)
    
    # 6. Save
    save_results(stats, trades, symbol, days)
