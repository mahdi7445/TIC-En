# -*- coding: utf-8 -*-
"""
Candle Signal Engine v4
========================
(کدنویسی و لاگ‌ها فارسی برای خودتان؛ تمام پیام‌هایی که به کاربر نهایی می‌رود انگلیسی است)

تغییر این نسخه نسبت به قبل:
  - حد ضرر ترکیبی: هرکدام از EMA7 کندل سیگنال یا آخرین سوینگ لو/های (۱۰ کندل قبل)
    که فاصله بیشتر و محافظه‌کارانه‌تری از قیمت ورود داشته باشد انتخاب می‌شود
    (برای لانگ: کمینه‌ی این دو مقدار؛ برای شورت: بیشینه‌ی این دو مقدار)

⚠️ درباره تایم‌فریم‌های 1m و 5m: طبق محدودیت سقف ۸۰۰ درخواست/روز Twelve Data،
این دو تایم‌فریم با تاخیر (به‌ترتیب هر ۳۰ و ۲۰ دقیقه) چک می‌شوند - هیچ سیگنالی از
دست نمی‌رود، فقط ممکن است چند دقیقه دیرتر شناسایی شود. جزئیات در README.
"""

import os
import time
import json
import logging
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("CandleEngineV4")

# ================== تنظیمات ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PRIVATE_CHANNEL_ID = os.getenv("PRIVATE_CHANNEL_ID", "").strip()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
FORCE_RUN_ALL = os.getenv("FORCE_RUN_ALL", "").strip() == "1"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "candle_state.json")
TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.json")
CHART_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_tmp.png")

HTTP_TIMEOUT = 30
TWELVEDATA_BASE = "https://api.twelvedata.com"

BOOTSTRAP_LIMIT = 300
COOLDOWN_BARS = 5
WHIPSAW_ATR_MULT = 0.5
EMA_SLOPE_ATR_MULT = 0.03
CANDLE_BODY_MAX_RATIO = 0.5
SHADOW_RATIO = 3.5
HIST_KEEP = 80          # برای ساخت چارت مستقیم از state (بدون درخواست اضافه)
SWING_LOOKBACK = 10      # چند کندل قبل از کندل سیگنال برای پیدا کردن آخرین سوینگ لو/های
SL_BUFFER_ATR_MULT = 0.3  # بافر اضافه فراتر از EMA7/سوینگ تا حد ضرر دقیقاً روی کف/سقف نباشد
RR_TARGETS = [1, 3, 5, 7]
TARGET_LABELS = {1: "Target 1", 3: "Target 2", 5: "Target 3", 7: "Target 4"}

WATCHLIST_SYMBOLS = {
    "BTC/USD": "BTC",
    "ETH/USD": "ETH",
    "XAU/USD": "GOLD",
}

TIMEFRAMES = {
    "1m":  {"td_interval": "1min",  "bar_seconds": 60,           "label": "1M"},
    "5m":  {"td_interval": "5min",  "bar_seconds": 5 * 60,       "label": "5M"},
    "15m": {"td_interval": "15min", "bar_seconds": 15 * 60,      "label": "15M"},
    "1h":  {"td_interval": "1h",    "bar_seconds": 60 * 60,      "label": "1H"},
    "4h":  {"td_interval": "4h",    "bar_seconds": 4 * 60 * 60,  "label": "4H"},
    "1d":  {"td_interval": "1day",  "bar_seconds": 24 * 60 * 60, "label": "1D"},
}


TF_CHECK_INTERVAL_SECONDS = {
    "1m": 30 * 60,
    "5m": 20 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def is_timeframe_due(tf_key: str, now_ts_: int, last_checked: Dict[str, int]) -> bool:
    """
    نسخه‌ی مقاوم در برابر تاخیر واقعی گیت‌هاب اکشنز: گیت‌هاب رسماً تضمین نمی‌کند کرون
    دقیقاً سر زمان تعیین‌شده اجرا شود (گاهی چند دقیقه دیرتر). به همین دلیل به‌جای تطبیق
    دقیق دقیقه/ساعت، بر اساس «چقدر زمان از آخرین چک این تایم‌فریم گذشته» تصمیم می‌گیریم.
    """
    if FORCE_RUN_ALL:
        return True
    interval = TF_CHECK_INTERVAL_SECONDS[tf_key]
    last = last_checked.get(tf_key, 0)
    return (now_ts_ - last) >= interval


# ================== ذخیره‌سازی وضعیت ==================

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"خطا در خواندن state: {e}")
    return {}


def save_state(state: Dict[str, Any]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log_trade_result(symbol: str, tf_key: str, trade: Dict[str, Any]):
    """وقتی معامله بسته می‌شود (استاپ/بریک‌ایون/تارگت نهایی)، نتیجه را برای محاسبه‌ی
    آماری/سود‌وزیان کاربران در subscription_bot.py ذخیره می‌کند."""
    os.makedirs(DATA_DIR, exist_ok=True)
    history = []
    if os.path.exists(TRADE_HISTORY_FILE):
        try:
            with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append({
        "symbol": symbol, "tf": tf_key, "side": trade["side"],
        "entry": trade["entry"], "sl": trade["sl"],
        "final_r": compute_final_r(trade),
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })
    history = history[-2000:]  # جلوگیری از رشد بی‌نهایت فایل
    with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ================== تلگرام ==================

def send_photo(caption: str, photo_path: Optional[str]) -> bool:
    if not TELEGRAM_BOT_TOKEN or not PRIVATE_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN or PRIVATE_CHANNEL_ID not set")
        return False
    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": PRIVATE_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": f}, timeout=60,
                )
        else:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": PRIVATE_CHANNEL_ID, "text": caption, "parse_mode": "HTML"}, timeout=30,
            )
        ok = resp.status_code == 200 and resp.json().get("ok")
        if not ok:
            logger.error(f"Telegram send error: {resp.text}")
        return bool(ok)
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")
        return False


# ================== داده Twelve Data ==================

def fetch_closed_klines(symbol: str, limit: int, interval: str, bar_seconds: int) -> List[Dict[str, Any]]:
    if not TWELVEDATA_API_KEY:
        return []
    url = f"{TWELVEDATA_BASE}/time_series"
    params = {"symbol": symbol, "interval": interval, "outputsize": limit, "timezone": "UTC", "apikey": TWELVEDATA_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        data = r.json()
    except Exception as e:
        logger.warning(f"Twelve Data request failed for {symbol} [{interval}]: {e}")
        return []
    if not isinstance(data, dict) or data.get("status") == "error" or "values" not in data:
        logger.warning(f"Twelve Data returned no data for {symbol} [{interval}]: {data}")
        return []
    now = time.time()
    candles = []
    for v in data["values"]:
        try:
            dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if dt.timestamp() + bar_seconds > now:
                continue
            candles.append({
                "open_time": int(dt.timestamp() * 1000), "dt": dt,
                "o": float(v["open"]), "h": float(v["high"]), "l": float(v["low"]), "c": float(v["close"]),
            })
        except Exception:
            continue
    candles.sort(key=lambda k: k["open_time"])
    return candles


# ================== منطق کندل سیگنال ==================

def new_candle_state() -> Dict[str, Any]:
    return {
        "ema7": None, "ema25": None, "atr": None, "tr_buffer": [],
        "hist": [], "trend_prev": "flat",
        "bull_used_this_trend": False, "bear_used_this_trend": False,
        "last_bull_bar_index": None, "last_bear_bar_index": None,
        "last_signal_price": None, "bar_index": 0, "last_open_time": None,
        "open_trade": None,
    }


def _ema_step(prev: Optional[float], price: float, length: int) -> float:
    if prev is None:
        return price
    alpha = 2.0 / (length + 1)
    return alpha * price + (1 - alpha) * prev


def step_candle_state(state: Dict[str, Any], o: float, h: float, l: float, c: float, open_time: int):
    s = dict(state)
    s["hist"] = list(s["hist"])
    s["tr_buffer"] = list(s["tr_buffer"])

    ema7_prev, ema25_prev = s["ema7"], s["ema25"]
    ema7_i = _ema_step(ema7_prev, c, 7)
    ema25_i = _ema_step(ema25_prev, c, 25)

    hist = s["hist"]
    prev_bar = hist[-1] if len(hist) >= 1 else None
    prev2_bar = hist[-2] if len(hist) >= 2 else None

    tr_i = max(h - l, abs(h - prev_bar["c"]), abs(l - prev_bar["c"])) if prev_bar else (h - l)
    if s["atr"] is None:
        s["tr_buffer"].append(tr_i)
        atr_i = sum(s["tr_buffer"][-14:]) / 14.0 if len(s["tr_buffer"]) >= 14 else None
    else:
        atr_i = (s["atr"] * 13 + tr_i) / 14.0

    body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    total_size = h - l

    is_uptrend = ema7_i > ema25_i
    is_downtrend = ema7_i < ema25_i

    is_valid_bull_candle = (body < CANDLE_BODY_MAX_RATIO * total_size) and (lower_shadow > SHADOW_RATIO * upper_shadow)
    is_valid_bear_candle = (body < CANDLE_BODY_MAX_RATIO * total_size) and (upper_shadow > SHADOW_RATIO * lower_shadow)

    next_invalidates_bull = (prev_bar is not None) and (prev_bar["l"] < l)
    next_invalidates_bear = (prev_bar is not None) and (prev_bar["h"] > h)

    ema7_slope = ema7_i - (ema7_prev if ema7_prev is not None else ema7_i)
    is_ema7_flat = True if atr_i is None else abs(ema7_slope) < (EMA_SLOPE_ATR_MULT * atr_i)

    bullish_engulf = bearish_engulf = False
    if prev_bar is not None:
        po, pc = prev_bar["o"], prev_bar["c"]
        bullish_engulf = (c > o) and (pc < po) and (c > po) and (o < pc)
        bearish_engulf = (c < o) and (pc > po) and (c < po) and (o > pc)

    bullish_pin = (lower_shadow > 2 * body) and (upper_shadow < body)
    bearish_pin = (upper_shadow > 2 * body) and (lower_shadow < body)

    both_above = both_below = False
    if prev_bar is not None and prev2_bar is not None:
        both_above = (prev_bar["c"] > prev_bar["ema7"]) and (prev2_bar["c"] > prev2_bar["ema7"])
        both_below = (prev_bar["c"] < prev_bar["ema7"]) and (prev2_bar["c"] < prev2_bar["ema7"])

    raw_bull = is_uptrend and is_valid_bull_candle and (not next_invalidates_bull) and (not is_ema7_flat) and both_above
    raw_bear = is_downtrend and is_valid_bear_candle and (not next_invalidates_bear) and (not is_ema7_flat) and both_below

    if is_uptrend and s["trend_prev"] != "up":
        s["bull_used_this_trend"] = False
    if is_downtrend and s["trend_prev"] != "down":
        s["bear_used_this_trend"] = False

    state_ok_bull = not s["bull_used_this_trend"]
    state_ok_bear = not s["bear_used_this_trend"]

    cooldown_ok_bull = (s["last_bull_bar_index"] is None) or (s["bar_index"] - s["last_bull_bar_index"] >= COOLDOWN_BARS)
    cooldown_ok_bear = (s["last_bear_bar_index"] is None) or (s["bar_index"] - s["last_bear_bar_index"] >= COOLDOWN_BARS)

    if s["last_signal_price"] is None or atr_i is None:
        price_move_ok = True
    else:
        price_move_ok = abs(c - s["last_signal_price"]) >= atr_i * WHIPSAW_ATR_MULT

    final_bull = raw_bull and state_ok_bull and cooldown_ok_bull and price_move_ok
    final_bear = raw_bear and state_ok_bear and cooldown_ok_bear and price_move_ok

    signal = None
    swing_window = hist[-SWING_LOOKBACK:] if hist else []

    if final_bull:
        s["bull_used_this_trend"] = True
        s["last_bull_bar_index"] = s["bar_index"]
        s["last_signal_price"] = c
        swing_low = min([b["l"] for b in swing_window]) if swing_window else l
        raw_sl = min(ema7_i, swing_low)  # فاصله بیشتر و محافظه‌کارانه‌تر از این دو
        buffer = SL_BUFFER_ATR_MULT * atr_i if atr_i else abs(c) * 0.001
        sl = raw_sl - buffer  # کمی پایین‌تر تا دقیقاً روی کف نباشد
        signal = {"side": "BUY", "confirmed": bool(bullish_engulf or bullish_pin), "price": c,
                  "open_time": open_time, "sl": sl}
    if final_bear:
        s["bear_used_this_trend"] = True
        s["last_bear_bar_index"] = s["bar_index"]
        s["last_signal_price"] = c
        swing_high = max([b["h"] for b in swing_window]) if swing_window else h
        raw_sl = max(ema7_i, swing_high)
        buffer = SL_BUFFER_ATR_MULT * atr_i if atr_i else abs(c) * 0.001
        sl = raw_sl + buffer  # کمی بالاتر تا دقیقاً روی سقف نباشد
        signal = {"side": "SELL", "confirmed": bool(bearish_engulf or bearish_pin), "price": c,
                  "open_time": open_time, "sl": sl}

    hist.append({"o": o, "h": h, "l": l, "c": c, "ema7": ema7_i, "dt_ms": open_time})
    s["hist"] = hist[-HIST_KEEP:]
    s["ema7"], s["ema25"], s["atr"] = ema7_i, ema25_i, atr_i
    s["trend_prev"] = "up" if is_uptrend else ("down" if is_downtrend else "flat")
    s["bar_index"] = s["bar_index"] + 1
    s["last_open_time"] = open_time

    return s, signal


# ================== ردیابی معامله باز و سطوح R:R ==================

def open_new_trade(signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = signal["price"]
    sl = signal["sl"]
    r = abs(entry - sl)
    if r <= 0:
        return None
    return {
        "side": signal["side"], "entry": entry, "sl": sl, "r": r,
        "hit": {str(t): False for t in RR_TARGETS}, "breakeven": False, "closed": False,
    }


def compute_final_r(trade: Dict[str, Any]) -> float:
    """تخمین ساده‌ی نتیجه‌ی نهایی معامله بر حسب R، با فرض:
    - در Target 1 فقط استاپ به نقطه ورود منتقل می‌شود (ریسک‌فری، بدون بستن پوزیشن)
    - در Target 3 (که RR=5 است) نیمی از پوزیشن بسته می‌شود
    - اگر تا Target 4 (RR=7) برود، نیمه‌ی باقی‌مانده هم آنجا بسته می‌شود
    """
    hit = trade["hit"]
    if not hit["1"]:
        return -1.0
    if not hit["5"]:
        return 0.0
    if not hit["7"]:
        return 2.5
    return 6.0


def check_open_trade(trade: Dict[str, Any], candle: Dict[str, Any]) -> List[Dict[str, Any]]:
    if trade is None or trade.get("closed"):
        return []
    events = []
    side, entry, sl, r = trade["side"], trade["entry"], trade["sl"], trade["r"]
    effective_sl = entry if trade.get("breakeven") else sl

    if side == "BUY":
        if candle["l"] <= effective_sl:
            trade["closed"] = True
            events.append({"type": "breakeven" if trade.get("breakeven") else "stop", "price": effective_sl})
            return events
        for target in RR_TARGETS:
            key = str(target)
            if not trade["hit"][key] and candle["h"] >= entry + target * r:
                trade["hit"][key] = True
                events.append({"type": "rr", "level": target, "price": entry + target * r})
                if target == 1:
                    trade["breakeven"] = True
                if target == max(RR_TARGETS):
                    trade["closed"] = True
    else:
        if candle["h"] >= effective_sl:
            trade["closed"] = True
            events.append({"type": "breakeven" if trade.get("breakeven") else "stop", "price": effective_sl})
            return events
        for target in RR_TARGETS:
            key = str(target)
            if not trade["hit"][key] and candle["l"] <= entry - target * r:
                trade["hit"][key] = True
                events.append({"type": "rr", "level": target, "price": entry - target * r})
                if target == 1:
                    trade["breakeven"] = True
                if target == max(RR_TARGETS):
                    trade["closed"] = True

    return events


# ================== چارت (از تاریخچه‌ی موجود در state، بدون درخواست اضافه) ==================

def build_chart_from_hist(hist: List[Dict[str, Any]], title: str, trade: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if len(hist) < 15:
        return None
    df = pd.DataFrame(hist)
    df["dt"] = pd.to_datetime(df["dt_ms"], unit="ms", utc=True)
    df = df.set_index("dt")
    ohlc = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["open", "high", "low", "close"]]
    apds = [mpf.make_addplot(df["ema7"], color="dodgerblue", width=1)]

    hlines_vals, hlines_colors, labels = [], [], []
    if trade:
        sign = 1 if trade["side"] == "BUY" else -1
        hlines_vals.append(trade["entry"]); hlines_colors.append("blue"); labels.append(("Entry", trade["entry"], "blue"))
        hlines_vals.append(trade["sl"]); hlines_colors.append("red"); labels.append(("Stop", trade["sl"], "red"))
        for t in RR_TARGETS:
            lvl = trade["entry"] + sign * t * trade["r"]
            hlines_vals.append(lvl); hlines_colors.append("green")
            labels.append((TARGET_LABELS[t], lvl, "green"))

    try:
        plot_kwargs = dict(type="candle", style="charles", addplot=apds, title=title, volume=False, returnfig=True)
        if hlines_vals:
            plot_kwargs["hlines"] = dict(hlines=hlines_vals, colors=hlines_colors, linestyle="--", linewidths=0.8)
        fig, axlist = mpf.plot(ohlc, **plot_kwargs)
        ax = axlist[0]
        x_right = len(ohlc) - 1
        for name, val, color in labels:
            ax.annotate(name, xy=(x_right, val), xytext=(5, 0), textcoords="offset points",
                        color=color, fontsize=8, va="center", fontweight="bold")
        fig.savefig(CHART_PATH, dpi=150, bbox_inches="tight")
        return CHART_PATH
    except Exception as e:
        logger.warning(f"Chart build failed: {e}")
        return None


# ================== پیام‌ها (انگلیسی، ساده و مینیمال) ==================

RISK_LINE = "\n\n⚠️ Please manage your risk and capital appropriately."
MOVE_TO_BE_LINE = "\n\n🔒 Please move your stop-loss to entry — this trade is now risk-free."
CLOSE_HALF_LINE = "\n\n✂️ Please close at least half of this position here."


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_entry_message(display: str, tf_label: str, signal: Dict[str, Any], trade: Dict[str, Any]) -> str:
    arrow = "🟢 LONG" if signal["side"] == "BUY" else "🔴 SHORT"
    sign = 1 if signal["side"] == "BUY" else -1
    targets_lines = "\n".join([f"🎯 {TARGET_LABELS[t]}: {trade['entry'] + sign * t * trade['r']:.2f}" for t in RR_TARGETS])
    return (
        f"{arrow} — {display} {tf_label}\n\n"
        f"Entry: <b>{signal['price']:.2f}</b>\n"
        f"❌ Stop: <b>{trade['sl']:.2f}</b>\n"
        f"{targets_lines}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_rr_exit_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    level = event["level"]
    label = TARGET_LABELS[level]
    extra = ""
    if level == 1:
        extra = MOVE_TO_BE_LINE
    elif level == 5:
        extra = CLOSE_HALF_LINE
    return (
        f"✅ {label} HIT — {display} {tf_label}\n\n"
        f"{label} reached on this {direction} trade.\n"
        f"Entry {trade['entry']:.2f}  ·  Stop {trade['sl']:.2f}  ·  Now {event['price']:.2f}\n\n"
        f"{_now_str()}"
        f"{extra}"
        f"{RISK_LINE}"
    )


def format_stop_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"❌ STOP HIT — {display} {tf_label}\n\n"
        f"Stop-loss hit on this {direction} trade.\n"
        f"Entry was {trade['entry']:.2f}  ·  Stop {trade['sl']:.2f}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


def format_breakeven_message(display: str, tf_label: str, trade: Dict[str, Any], event: Dict[str, Any]) -> str:
    direction = "LONG" if trade["side"] == "BUY" else "SHORT"
    return (
        f"⚪ BREAKEVEN — {display} {tf_label}\n\n"
        f"Price returned to entry after reaching Target 1 on this {direction} trade — closed with no loss.\n"
        f"Entry {trade['entry']:.2f}\n\n"
        f"{_now_str()}"
        f"{RISK_LINE}"
    )


# ================== حلقه اصلی ==================

def process_symbol_timeframe(symbol: str, display: str, tf_key: str, tf_cfg: Dict[str, Any],
                              candle_states: Dict[str, Any]) -> None:
    state_key = f"{symbol}|{tf_key}"
    sym_state = candle_states.get(state_key)

    if sym_state is None:
        candles = fetch_closed_klines(symbol, BOOTSTRAP_LIMIT, tf_cfg["td_interval"], tf_cfg["bar_seconds"])
        if len(candles) < 30:
            logger.warning(f"Not enough data for {symbol} [{tf_key}]")
            return
        state = new_candle_state()
        last_idx = len(candles) - 1
        for idx, k in enumerate(candles):
            state, sig = step_candle_state(state, k["o"], k["h"], k["l"], k["c"], k["open_time"])
            if idx == last_idx and sig:
                trade = open_new_trade(sig)
                if trade:
                    state["open_trade"] = trade
                    _send_entry(display, tf_key, tf_cfg, sig, trade, state["hist"])
        candle_states[state_key] = state
        return

    last_open_time = sym_state.get("last_open_time")
    candles = fetch_closed_klines(symbol, 10, tf_cfg["td_interval"], tf_cfg["bar_seconds"])
    new_candles = [k for k in candles if last_open_time is None or k["open_time"] > last_open_time]

    state = sym_state
    for k in new_candles:
        if state.get("open_trade"):
            events = check_open_trade(state["open_trade"], k)
            for ev in events:
                _send_exit(display, tf_key, tf_cfg, state["open_trade"], ev, state["hist"], symbol)

        state, sig = step_candle_state(state, k["o"], k["h"], k["l"], k["c"], k["open_time"])
        if sig:
            trade = open_new_trade(sig)
            if trade:
                state["open_trade"] = trade
                _send_entry(display, tf_key, tf_cfg, sig, trade, state["hist"])

    candle_states[state_key] = state


def _send_entry(display, tf_key, tf_cfg, sig, trade, hist):
    chart = build_chart_from_hist(hist, f"{display} {tf_cfg['label']} · Entry", trade=trade)
    msg = format_entry_message(display, tf_cfg["label"], sig, trade)
    if send_photo(msg, chart):
        logger.info(f"📤 Entry sent: {display} [{tf_key}] {sig['side']}")
    time.sleep(1.5)


def _send_exit(display, tf_key, tf_cfg, trade, event, hist, symbol):
    if event["type"] == "stop":
        title_suffix = "Stop"
    elif event["type"] == "breakeven":
        title_suffix = "Breakeven"
    else:
        title_suffix = TARGET_LABELS[event["level"]]
    title = f"{display} {tf_cfg['label']} · {title_suffix}"
    chart = build_chart_from_hist(hist, title, trade=trade)
    if event["type"] == "stop":
        msg = format_stop_message(display, tf_cfg["label"], trade, event)
    elif event["type"] == "breakeven":
        msg = format_breakeven_message(display, tf_cfg["label"], trade, event)
    else:
        msg = format_rr_exit_message(display, tf_cfg["label"], trade, event)
    if send_photo(msg, chart):
        logger.info(f"📤 Exit sent: {display} [{tf_key}] {event['type']}")
    if trade.get("closed"):
        log_trade_result(symbol, tf_key, trade)
    time.sleep(1.5)



def main():
    if not TELEGRAM_BOT_TOKEN or not PRIVATE_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN or PRIVATE_CHANNEL_ID not set - exiting")
        return
    if not TWELVEDATA_API_KEY:
        logger.error("TWELVEDATA_API_KEY not set - exiting")
        return

    now_ts_ = int(datetime.now(timezone.utc).timestamp())
    state = load_state()
    candle_states = state.setdefault("candle_signals", {})
    last_checked = state.setdefault("tf_last_checked", {})

    due_tfs = [tf for tf in TIMEFRAMES if is_timeframe_due(tf, now_ts_, last_checked)]
    logger.info(f"Due timeframes this run: {due_tfs or '(none)'}")

    for tf_key in due_tfs:
        last_checked[tf_key] = now_ts_

    for symbol, display in WATCHLIST_SYMBOLS.items():
        for tf_key in due_tfs:
            tf_cfg = TIMEFRAMES[tf_key]
            try:
                process_symbol_timeframe(symbol, display, tf_key, tf_cfg, candle_states)
                time.sleep(8)
            except Exception as e:
                logger.error(f"Error processing {symbol} [{tf_key}]: {e}")

    save_state(state)
    logger.info("✅ Scan complete")


if __name__ == "__main__":
    main()
