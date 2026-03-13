"""
ConsiderTrader — Analysis Engine
════════════════════════════════════════════════
Data Sources (All Free, No API Key):
  • Binance Public API   → Price, Volume, Klines (1H + 4H)
  • Binance Futures API  → Funding Rate, Long/Short, Open Interest
  • Alternative.me       → Fear & Greed Index
  • DefiLlama            → ETH Stablecoin Flow (wallet inflow/outflow proxy)

Strategy:
  Asset      : BTC/USDT + ETH/USDT
  Timeframe  : 1H entry + 4H trend filter
  Leverage   : 5x
  Risk/Trade : 2%
  Stop       : ATR x 1.5
  Target     : ATR x 3.0  →  1:2 risk/reward
  Signal     : 4 out of 5 conditions + 4H trend up
════════════════════════════════════════════════
"""

from flask import Flask, render_template, jsonify
import requests
import numpy as np
from datetime import datetime
import threading
import time

app = Flask(__name__)

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
CONFIG = {
    "risk_per_trade": 0.02,
    "leverage":       5,
    "atr_stop_mult":  1.5,
    "atr_tp_mult":    3.0,
    "min_conditions": 4,
    "cache_ttl":      30,
    "klines_limit":   100,
}

ASSETS = ["BTCUSDT", "ETHUSDT"]

# ─────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────

def get_klines(symbol="BTCUSDT", interval="1h", limit=100):
    """Binance candle data"""
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        data = r.json()
        if not isinstance(data, list):
            return [], [], [], [], []
        return (
            [float(d[4]) for d in data],
            [float(d[2]) for d in data],
            [float(d[3]) for d in data],
            [float(d[5]) for d in data],
            [d[0]        for d in data],
        )
    except Exception as e:
        print(f"[klines:{symbol}:{interval}] {e}")
        return [], [], [], [], []


def get_ticker(symbol="BTCUSDT"):
    """Live price + 24H summary"""
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}",
            timeout=10
        )
        d = r.json()
        return {
            "price":      float(d["lastPrice"]),
            "change_pct": float(d["priceChangePercent"]),
            "volume_24h": float(d["quoteVolume"]),
            "high_24h":   float(d["highPrice"]),
            "low_24h":    float(d["lowPrice"]),
        }
    except Exception as e:
        print(f"[ticker:{symbol}] {e}")
        return {}


def get_funding_rate(symbol="BTCUSDT"):
    """
    Binance Futures funding rate.
    Healthy: Between -0.05% and +0.05%
    Above +0.08% → market overheated
    Negative     → fear exists, short squeeze risk
    """
    try:
        r    = requests.get(
            f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1",
            timeout=10
        )
        data = r.json()
        if data and isinstance(data, list):
            return round(float(data[-1]["fundingRate"]) * 100, 4)
        return 0.0
    except Exception as e:
        print(f"[funding:{symbol}] {e}")
        return 0.0


def get_open_interest(symbol="BTCUSDT"):
    """Open position size"""
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
            timeout=10
        )
        return float(r.json().get("openInterest", 0))
    except Exception as e:
        print(f"[OI:{symbol}] {e}")
        return 0.0


def get_long_short_ratio(symbol="BTCUSDT"):
    """
    Global Long/Short ratio.
    Healthy: Long 40%–65%
    Long >70% → everyone is long, caution
    Long <35% → everyone is short, upward squeeze risk
    """
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1},
            timeout=10
        )
        data = r.json()
        if data and isinstance(data, list):
            return (
                round(float(data[0]["longAccount"]) * 100, 1),
                round(float(data[0]["shortAccount"]) * 100, 1),
                round(float(data[0]["longShortRatio"]), 3),
            )
        return 50.0, 50.0, 1.0
    except Exception as e:
        print(f"[L/S:{symbol}] {e}")
        return 50.0, 50.0, 1.0


def get_fear_greed():
    """
    Alternative.me Fear & Greed (0–100)
    0–24  : Extreme Fear   → potential buying opportunity
    75–100: Extreme Greed  → be careful
    """
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        d = r.json()
        return int(d["data"][0]["value"]), d["data"][0]["value_classification"]
    except Exception as e:
        print(f"[fear&greed] {e}")
        return 50, "Neutral"


def get_defillama_stablecoin_flow():
    """
    DefiLlama — ETH chain stablecoin flow.
    Increase → money entering market (buying pressure proxy)
    Decrease → money leaving market (selling pressure proxy)
    Direct for ETH, general liquidity indicator for BTC.
    """
    try:
        r    = requests.get(
            "https://stablecoins.llama.fi/stablecoincharts/ethereum",
            timeout=10
        )
        data = r.json()
        if not data or len(data) < 2:
            return 0.0, "no data"

        latest   = float(data[-1].get("totalCirculatingUSD", {}).get("peggedUSD", 0))
        previous = float(data[-2].get("totalCirculatingUSD", {}).get("peggedUSD", 0))

        if previous == 0:
            return 0.0, "neutral"

        change_pct = ((latest - previous) / previous) * 100

        if change_pct > 0.5:
            label = "INFLOW 🟢"
        elif change_pct < -0.5:
            label = "OUTFLOW 🔴"
        else:
            label = "NEUTRAL ⚪"

        return round(change_pct, 3), label
    except Exception as e:
        print(f"[defillama] {e}")
        return 0.0, "no connection"


# ─────────────────────────────────────────────
# ANALYSIS ENGINE
# ─────────────────────────────────────────────

def calc_atr(highs, lows, closes, period=14):
    """ATR normalize (100 = average, >140 danger, <80 opportunity)"""
    if len(closes) < period + 1:
        return 100.0, 0.0
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]))
        for i in range(1, len(closes))
    ]
    atr = np.mean(trs[:period])
    atr_series = [atr]
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        atr_series.append(atr)

    current  = atr_series[-1]
    baseline = np.mean(atr_series[-period*3:]) if len(atr_series) >= period*3 else np.mean(atr_series)
    norm     = (current / baseline) * 100 if baseline > 0 else 100.0
    return round(norm, 1), round(current, 2)


def calc_bb_width(closes, period=20, mult=2.0):
    """Bollinger Band width normalize"""
    if len(closes) < period * 2:
        return 100.0
    recent   = closes[-period:]
    mid      = np.mean(recent)
    std      = np.std(recent)
    width    = (2 * mult * std) / (mid + 1e-9) * 100
    all_w    = [
        (2 * mult * np.std(closes[i-period:i])) / (np.mean(closes[i-period:i]) + 1e-9) * 100
        for i in range(period, len(closes))
    ]
    baseline = np.mean(all_w) if all_w else width
    return round((width / baseline) * 100 if baseline > 0 else 100.0, 1)


def calc_obv(closes, volumes):
    """OBV normalize — accumulation/distribution detection"""
    if len(closes) < 20:
        return 0.0
    obv, series = 0, [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            obv -= volumes[i]
        series.append(obv)
    smoothed = np.convolve(series, np.ones(10)/10, mode='valid')
    baseline = np.mean(smoothed[-40:]) if len(smoothed) >= 40 else np.mean(smoothed)
    return round((smoothed[-1] - baseline) / (abs(baseline) + 1) * 100, 1)


def calc_cvd(closes, highs, lows, volumes):
    """CVD normalize — instant buying/selling pressure"""
    if len(closes) < 20:
        return 0.0
    deltas = []
    for i in range(len(closes)):
        rng = highs[i] - lows[i]
        if rng == 0:
            deltas.append(0)
            continue
        buy_vol = ((closes[i] - lows[i]) / rng) * volumes[i]
        deltas.append(buy_vol - (volumes[i] - buy_vol))
    cvd      = np.cumsum(deltas)
    window   = cvd[-60:] if len(cvd) >= 60 else cvd
    baseline = np.mean(window)
    std      = np.std(window) + 1
    return round((cvd[-1] - baseline) / std * 50, 1)


def calc_4h_trend(closes_4h):
    """
    4H trend filter — EMA20 vs EMA50.
    Counter-trend trade = gambling. Enter only in trend direction.
    """
    if len(closes_4h) < 50:
        return "NEUTRAL →", True
    ema20 = float(np.mean(closes_4h[-20:]))
    ema50 = float(np.mean(closes_4h[-50:]))
    if ema20 > ema50 * 1.005:
        return "UP ↑", True
    elif ema20 < ema50 * 0.995:
        return "DOWN ↓", False
    return "NEUTRAL →", True


def generate_signal(vol_score, flow_score, funding, long_pct, fg_value, trend_ok):
    """
    5 condition evaluation + 4H trend filter.
    If 4H trend is down, no signal is generated.
    """
    conditions = {
        "vol_normal":   vol_score < 120,
        "flow_pozitif": flow_score > 30,
        "funding_iyi":  -0.05 < funding < 0.05,
        "ls_dengeli":   40 < long_pct < 65,
        "fg_saglikli":  fg_value > 30,
    }
    score = sum(conditions.values())

    if not trend_ok:
        return "TREND DOWN — WAIT ⛔", "#666666", score
    if score >= 5:
        return "STRONG LONG 🟢", "#00ff88", score
    elif score >= 4 and flow_score > 20:
        return "LONG PREP 🟡", "#00cc66", score
    elif flow_score < -40 or vol_score > 150:
        return "AVOID / WAIT 🔴", "#ff4444", score
    elif funding > 0.08:
        return "OVERHEATED ⚠️", "#ff8800", score
    elif score >= 3:
        return "NEUTRAL WATCH ⚪", "#888888", score
    else:
        return "WEAK CONDITIONS 🔴", "#ff6600", score


def calc_stop_tp(price, atr):
    """Stop and TP calculation — ATR based, fixed R/R"""
    stop     = round(price - atr * CONFIG["atr_stop_mult"], 2)
    tp       = round(price + atr * CONFIG["atr_tp_mult"], 2)
    stop_pct = round((price - stop) / price * 100, 2)
    tp_pct   = round((tp - price) / price * 100, 2)
    return stop, tp, stop_pct, tp_pct


# ─────────────────────────────────────────────
# MAIN DATA BUILDER
# ─────────────────────────────────────────────

def build_asset_data(symbol):
    closes, highs, lows, volumes, times = get_klines(symbol, "1h", CONFIG["klines_limit"])
    if not closes:
        return {}

    closes_4h, _, _, _, _ = get_klines(symbol, "4h", 60)
    trend_label, trend_ok = calc_4h_trend(closes_4h)

    vol_score, atr_val = calc_atr(highs, lows, closes)
    bb_score           = calc_bb_width(closes)
    combined_vol       = round((vol_score + bb_score) / 2, 1)

    obv_norm = calc_obv(closes, volumes)
    cvd_norm = calc_cvd(closes, highs, lows, volumes)
    flow     = round(obv_norm * 0.4 + cvd_norm * 0.6, 1)

    ticker             = get_ticker(symbol)
    funding            = get_funding_rate(symbol)
    long_pct, short_pct, ls_ratio = get_long_short_ratio(symbol)
    oi                 = get_open_interest(symbol)
    fg_value, fg_label = get_fear_greed()
    dl_change, dl_label = get_defillama_stablecoin_flow()

    signal, signal_color, sig_score = generate_signal(
        combined_vol, flow, funding, long_pct, fg_value, trend_ok
    )

    current_price = ticker.get("price", closes[-1])
    stop, tp, stop_pct, tp_pct = calc_stop_tp(current_price, atr_val)

    # Labels
    if combined_vol > 140:
        vol_label, vol_color = "HIGH 🔴", "#ff4444"
    elif combined_vol < 80:
        vol_label, vol_color = "LOW 🔵", "#4499ff"
    else:
        vol_label, vol_color = "NORMAL ⚪", "#888888"

    if flow > 40:
        flow_label, flow_color = "STRONG BUY 🟢", "#00ff88"
    elif flow > 15:
        flow_label, flow_color = "BUY PRESSURE 🟢", "#00cc66"
    elif flow < -40:
        flow_label, flow_color = "STRONG SELL 🔴", "#ff4444"
    elif flow < -15:
        flow_label, flow_color = "SELL PRESSURE 🔴", "#ff6666"
    else:
        flow_label, flow_color = "NEUTRAL ⚪", "#888888"

    return {
        "symbol":        symbol,
        "price":         ticker.get("price", 0),
        "change_pct":    ticker.get("change_pct", 0),
        "volume_24h":    ticker.get("volume_24h", 0),
        "high_24h":      ticker.get("high_24h", 0),
        "low_24h":       ticker.get("low_24h", 0),
        "vol_score":     combined_vol,
        "vol_label":     vol_label,
        "vol_color":     vol_color,
        "atr":           atr_val,
        "flow_score":    flow,
        "flow_label":    flow_label,
        "flow_color":    flow_color,
        "obv":           obv_norm,
        "cvd":           cvd_norm,
        "funding":       funding,
        "long_pct":      long_pct,
        "short_pct":     short_pct,
        "ls_ratio":      ls_ratio,
        "open_interest": round(oi, 0),
        "fg_value":      fg_value,
        "fg_label":      fg_label,
        "dl_change":     dl_change,
        "dl_label":      dl_label,
        "trend_label":   trend_label,
        "trend_ok":      trend_ok,
        "signal":        signal,
        "signal_color":  signal_color,
        "sig_score":     int(sig_score),
        "stop":          stop,
        "tp":            tp,
        "stop_pct":      stop_pct,
        "tp_pct":        tp_pct,
        "rr_ratio":      round(CONFIG["atr_tp_mult"] / CONFIG["atr_stop_mult"], 1),
        "closes":        closes[-50:],
        "times":         times[-50:],
        "updated":       datetime.now().strftime("%H:%M:%S"),
    }


# ─────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────
cache = {"BTCUSDT": {}, "ETHUSDT": {}, "last_update": 0}

def refresh_cache():
    while True:
        try:
            for symbol in ASSETS:
                cache[symbol] = build_asset_data(symbol)
            cache["last_update"] = time.time()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Cache updated")
        except Exception as e:
            print(f"[cache] Error: {e}")
        time.sleep(CONFIG["cache_ttl"])


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/data")
def api_data():
    age = time.time() - cache.get("last_update", 0)
    return jsonify({
        "btc":     cache.get("BTCUSDT", {}),
        "eth":     cache.get("ETHUSDT", {}),
        "age":     round(age, 0),
        "next_in": max(0, round(CONFIG["cache_ttl"] - age, 0)),
    })

@app.route("/api/btc")
def api_btc():
    return jsonify(cache.get("BTCUSDT", {}))

@app.route("/api/eth")
def api_eth():
    return jsonify(cache.get("ETHUSDT", {}))

@app.route("/api/config")
def api_config():
    return jsonify(CONFIG)


# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🚀 ConsiderTrader starting...")
    print(f"   Assets   : {', '.join(ASSETS)}")
    print(f"   Risk     : {CONFIG['risk_per_trade']*100}% | Leverage: {CONFIG['leverage']}x")
    print(f"   Stop     : ATR x{CONFIG['atr_stop_mult']} | Target: ATR x{CONFIG['atr_tp_mult']}")
    print(f"   Update   : every {CONFIG['cache_ttl']}s\n")

    for symbol in ASSETS:
        print(f"📡 {symbol} fetching...")
        cache[symbol] = build_asset_data(symbol)

    cache["last_update"] = time.time()
    print("\n✅ Ready → http://localhost:5005\n")

    threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(debug=False, port=5005, host="0.0.0.0")