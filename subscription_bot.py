# -*- coding: utf-8 -*-
"""
Subscription Bot v4
(کدنویسی و کامنت‌ها فارسی برای شما؛ تمام پیام‌هایی که کاربر می‌بیند انگلیسی است)

اضافه‌شده نسبت به نسخه قبل:
  - چند ادمین هم‌زمان (ADMIN_USER_IDS)
  - سیستم کد تخفیف قابل‌مدیریت توسط ادمین (درصدی، محدودیت تعداد استفاده، تاریخ انقضا)
  - دکمه «✅ I've Paid» برای تایید آنی پرداخت به‌جای منتظرماندن تا اجرای بعدی کرون
  - پیام‌ها کوتاه‌تر، مینیمال‌تر و خواناتر
"""

import os
import json
import random
import time
import requests
from datetime import datetime, timezone

# ================== تنظیمات پایه ==================
BOT_TOKEN = os.environ["BOT_TOKEN"]
PRIVATE_CHANNEL_ID = os.environ["PRIVATE_CHANNEL_ID"]
PUBLIC_CHANNEL_LINK = os.environ.get("PUBLIC_CHANNEL_LINK", "")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "")  # مثلا @your_support_username
ADMIN_USER_IDS = {int(x) for x in os.environ.get("ADMIN_USER_IDS", "").replace(" ", "").split(",") if x}
REFERRAL_REWARD_DAYS = int(os.environ.get("REFERRAL_REWARD_DAYS") or "7")

WALLET_TRC20 = os.environ["WALLET_ADDRESS_TRC20"]
WALLET_BEP20 = os.environ.get("WALLET_ADDRESS_BEP20", "")
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY", "")

PAYMENT_WINDOW_MINUTES = int(os.environ.get("PAYMENT_WINDOW_MINUTES") or "45")

LONG_POLL_SECONDS = 25                                   # هر getUpdates تا ۲۵ ثانیه منتظر پیام جدید می‌مونه (آنی)
LOOP_MAX_SECONDS = int(os.environ.get("LOOP_MAX_SECONDS") or str(5 * 3600 + 20 * 60))  # ۵ ساعت و ۲۰ دقیقه پیش‌فرض
GIT_COMMIT_EVERY_SECONDS = 120                            # هر ۲ دقیقه تغییرات commit می‌شود

# ================== پلن‌ها — قیمت کمی زیر میانگین بازار، با تخفیف پلکانی برای تشویق اشتراک بلندمدت ==================
PLANS = {
    "1m":  {"label": "1 Month",   "days": 30,  "usd": 39},
    "3m":  {"label": "3 Months",  "days": 90,  "usd": 99},   # ~15% off monthly rate
    "6m":  {"label": "6 Months",  "days": 180, "usd": 180},  # ~23% off
    "12m": {"label": "12 Months", "days": 365, "usd": 300},  # ~36% off
}

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"

NETWORKS = {
    "trc20": {"label": "USDT (TRC20 - Tron)", "wallet": WALLET_TRC20, "decimals": 6},
    "bep20": {"label": "USDT (BEP20 - BNB Chain)", "wallet": WALLET_BEP20, "decimals": 18},
}

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

WELCOME_TEXT = (
    "👋 <b>Welcome to the Signal Room</b>\n\n"
    "Rule-based entries on <b>BTC, ETH &amp; Gold</b> — every timeframe, 1-minute to daily.\n"
    "Every call includes entry, stop-loss, 4 profit targets, and a chart.\n\n"
    "✅ Every signal generated gets posted — wins and losses alike\n"
    f"{('👀 Free samples: ' + PUBLIC_CHANNEL_LINK) if PUBLIC_CHANNEL_LINK else ''}\n\n"
    "Use the buttons below to get started 👇"
)

HELP_TEXT = (
    "<b>Commands</b>\n\n"
    "/plans — view plans &amp; subscribe\n"
    "/status — your subscription status\n"
    "/results — signal performance stats\n"
    "/pnl AMOUNT — estimate your P&amp;L (e.g. /pnl 100)\n"
    "/code CODE — apply a discount code\n"
    "/refer — get your referral link\n"
    "/support — contact support\n"
    "/disclaimer — risk disclaimer\n"
    "/cancel — cancel your membership\n"
    "/help — this message"
)

DISCLAIMER_TEXT = (
    "⚠️ <b>Risk Disclaimer</b>\n\n"
    "Signals shared here are for educational and informational purposes only and are "
    "<b>not financial advice</b>. Trading cryptocurrencies and other assets involves "
    "substantial risk of loss and is not suitable for everyone.\n\n"
    "Past performance (including the stats shown in /results) does not guarantee future "
    "results. Always do your own research and never risk more than you can afford to lose.\n\n"
    "By using this service you acknowledge that you are solely responsible for your own "
    "trading decisions."
)


def support_text():
    lines = ["💬 <b>Need help?</b>", ""]
    if ADMIN_USER_IDS:
        # لینک tg://user?id= یک لینک قابل‌کلیک تلگرامیه که چت با اون کاربر رو باز می‌کنه،
        # حتی بدون داشتن یوزرنیم عمومی.
        admin_links = [f'<a href="tg://user?id={aid}">Admin{"" if len(ADMIN_USER_IDS) == 1 else " " + str(i+1)}</a>'
                       for i, aid in enumerate(sorted(ADMIN_USER_IDS))]
        lines.append("Tap to message: " + "  ·  ".join(admin_links))
    if SUPPORT_CONTACT:
        lines.append(f"Or contact: {SUPPORT_CONTACT}")
    if not ADMIN_USER_IDS and not SUPPORT_CONTACT:
        lines.append("Please message the channel admin directly for support.")
    return "\n".join(lines)


def main_menu_keyboard():
    return [
        [{"text": "💎 View Plans", "callback_data": "menu:plans"}],
        [{"text": "📊 My Subscription", "callback_data": "menu:status"}],
        [{"text": "📈 Signal Results", "callback_data": "menu:results"}, {"text": "🧮 Calculate P&L", "callback_data": "menu:pnl"}],
        [{"text": "🤝 Refer a Friend", "callback_data": "menu:refer"}],
        [{"text": "💬 Support", "callback_data": "menu:support"}, {"text": "⚠️ Disclaimer", "callback_data": "menu:disclaimer"}],
    ]

# ================== توابع کمکی JSON ==================

def _load(name, default):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def fmt_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def days_remaining(expiry_ts):
    seconds = expiry_ts - now_ts()
    return max(0, seconds // (24 * 3600))


# ================== آمار عملکرد سیگنال‌ها (مشترک با candle_engine.py) ==================

def load_trade_history():
    return _load("trade_history.json", [])


def compute_stats(history):
    if not history:
        return None
    total = len(history)
    wins = sum(1 for h in history if h["final_r"] > 0)
    breakeven = sum(1 for h in history if h["final_r"] == 0)
    losses = sum(1 for h in history if h["final_r"] < 0)
    total_r = sum(h["final_r"] for h in history)
    return {
        "total": total, "wins": wins, "breakeven": breakeven, "losses": losses,
        "total_r": total_r, "avg_r": total_r / total, "win_rate": wins / total * 100,
    }


def format_results_message():
    history = load_trade_history()
    stats = compute_stats(history)
    if not stats:
        return "📊 No closed signals yet — check back soon!"
    return (
        f"📊 <b>Signal Performance</b>\n"
        f"<i>Last {stats['total']} closed trades — every signal we've posted, no cherry-picking</i>\n\n"
        f"✅ Wins: {stats['wins']} ({stats['win_rate']:.0f}%)\n"
        f"⚪ Breakeven: {stats['breakeven']}\n"
        f"❌ Losses: {stats['losses']}\n\n"
        f"Total: <b>{stats['total_r']:+.1f}R</b>\n"
        f"Average: <b>{stats['avg_r']:+.2f}R</b> per trade"
    )


def format_pnl_message(risk_amount: float):
    history = load_trade_history()
    stats = compute_stats(history)
    if not stats:
        return "📊 No closed signals yet to calculate from — check back soon!"
    total_pnl = stats["total_r"] * risk_amount
    avg_pnl = stats["avg_r"] * risk_amount
    return (
        f"🧮 <b>P&amp;L Estimate</b> — risking ${risk_amount:.0f} per trade\n\n"
        f"Based on {stats['total']} closed trades:\n"
        f"Total: <b>${total_pnl:+.2f}</b>\n"
        f"Average per trade: <b>${avg_pnl:+.2f}</b>\n\n"
        f"<i>Assumes you followed the recommended risk management: move stop to "
        f"breakeven at Target 1, close half the position at Target 3. This is an "
        f"estimate, not a guarantee of future results.</i>"
    )


def pnl_keyboard():
    return [
        [{"text": "$50", "callback_data": "pnl:50"}, {"text": "$100", "callback_data": "pnl:100"}, {"text": "$200", "callback_data": "pnl:200"}],
        [{"text": "⬅️ Back", "callback_data": "menu:home"}],
    ]


def format_status_message(subscribers, user_id):
    active = next((s for s in subscribers if s["user_id"] == user_id), None)
    if not active:
        return "❌ <b>No active subscription</b>\n\nType /plans to subscribe."
    remaining = days_remaining(active["expiry_ts"])
    warn = "\n\n⏰ Your subscription ends soon — type /plans to renew and avoid interruption." if remaining <= 3 else ""
    return (
        f"✅ <b>Active subscription</b>\n\n"
        f"Expires: {fmt_date(active['expiry_ts'])}\n"
        f"Days remaining: <b>{remaining}</b>\n\n"
        f"/plans to renew or upgrade  ·  /cancel to end membership"
        f"{warn}"
    )


# ================== توابع تلگرام ==================

def tg(method, **params):
    # هر متد ممکنه پارامتر تلگرامیِ خودش هم اسمش timeout باشه (برای long-polling)،
    # پس timeout درخواست HTTP خودمون رو جدا و همیشه چند ثانیه بیشتر از اون می‌گیریم.
    http_timeout = params.get("timeout", 0) + 10
    resp = requests.post(f"{API}/{method}", json=params, timeout=http_timeout)
    resp.raise_for_status()
    return resp.json()


def send_message(chat_id, text, keyboard=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        tg("sendMessage", **params)
    except Exception as e:
        print(f"[warn] send_message failed for {chat_id}: {e}")


def edit_message(chat_id, message_id, text, keyboard=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        tg("editMessageText", **params)
    except Exception as e:
        print(f"[warn] edit_message failed for {chat_id}: {e}")


def answer_callback(callback_id, text=""):
    try:
        tg("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except Exception as e:
        print(f"[warn] answer_callback failed: {e}")


def create_invite_link(expire_minutes=60):
    expire_date = now_ts() + expire_minutes * 60
    result = tg("createChatInviteLink", chat_id=PRIVATE_CHANNEL_ID, expire_date=expire_date, member_limit=1)
    return result["result"]["invite_link"]


def remove_member(user_id):
    try:
        tg("banChatMember", chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        tg("unbanChatMember", chat_id=PRIVATE_CHANNEL_ID, user_id=user_id, only_if_banned=True)
    except Exception as e:
        print(f"[warn] remove_member failed for {user_id}: {e}")


# ================== بررسی تراکنش‌های ورودی روی هر شبکه ==================

def fetch_trc20_transfers(limit=50):
    if not WALLET_TRC20:
        return []
    url = f"https://api.trongrid.io/v1/accounts/{WALLET_TRC20}/transactions/trc20"
    params = {"limit": limit, "contract_address": USDT_TRC20_CONTRACT, "only_to": "true", "order_by": "block_timestamp,desc"}
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        out = []
        for tx in r.json().get("data", []):
            out.append({
                "tx_id": tx.get("transaction_id"), "to": tx.get("to", "").lower(),
                "value": int(tx.get("value", "0")), "ts": int(tx.get("block_timestamp", 0)) / 1000, "network": "trc20",
            })
        return out
    except Exception as e:
        print(f"[warn] fetch_trc20_transfers failed: {e}")
        return []


def fetch_bep20_transfers(limit=50):
    if not WALLET_BEP20 or not BSCSCAN_API_KEY:
        return []
    url = "https://api.bscscan.com/api"
    params = {
        "module": "account", "action": "tokentx", "contractaddress": USDT_BEP20_CONTRACT,
        "address": WALLET_BEP20, "sort": "desc", "apikey": BSCSCAN_API_KEY, "page": 1, "offset": limit,
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "1":
            return []
        out = []
        for tx in data.get("result", []):
            out.append({
                "tx_id": tx.get("hash"), "to": tx.get("to", "").lower(),
                "value": int(tx.get("value", "0")), "ts": int(tx.get("timeStamp", 0)), "network": "bep20",
            })
        return out
    except Exception as e:
        print(f"[warn] fetch_bep20_transfers failed: {e}")
        return []


def units_to_usdt(value, decimals):
    return value / (10 ** decimals)


def fetch_all_transfers():
    return fetch_trc20_transfers() + fetch_bep20_transfers()


# ================== تخفیف ==================

_BOT_USERNAME_CACHE = {"value": None}


def get_bot_username():
    if _BOT_USERNAME_CACHE["value"]:
        return _BOT_USERNAME_CACHE["value"]
    try:
        result = tg("getMe")
        username = result["result"]["username"]
        _BOT_USERNAME_CACHE["value"] = username
        return username
    except Exception as e:
        print(f"[warn] get_bot_username failed: {e}")
        return None


def referral_text(user_id):
    username = get_bot_username()
    if not username:
        return "🤝 Referral links are temporarily unavailable — please try again in a moment."
    link = f"https://t.me/{username}?start=ref_{user_id}"
    return (
        f"🤝 <b>Refer a Friend</b>\n\n"
        f"Share your link — when a friend you invite makes their <b>first purchase</b>, "
        f"you get <b>{REFERRAL_REWARD_DAYS} free days</b> added to your subscription automatically.\n\n"
        f"Your link:\n<code>{link}</code>"
    )


def find_discount(discounts, code):
    code = code.strip().upper()
    for d in discounts:
        if d["code"] == code and d.get("active", True):
            if d.get("expires_at") and now_ts() > d["expires_at"]:
                continue
            if d.get("max_uses") and d.get("used", 0) >= d["max_uses"]:
                continue
            return d
    return None


# ================== کیبوردها ==================

def plans_keyboard():
    rows = []
    for key, p in PLANS.items():
        monthly = p["usd"] / (p["days"] / 30)
        rows.append([{"text": f"💎 {p['label']} — ${p['usd']} (${monthly:.0f}/mo)", "callback_data": f"plan:{key}"}])
    rows.append([{"text": "⬅️ Back to Menu", "callback_data": "menu:home"}])
    return rows


def networks_keyboard(plan_key):
    rows = []
    for key, n in NETWORKS.items():
        if not n["wallet"]:
            continue
        rows.append([{"text": f"💳 {n['label']}", "callback_data": f"net:{key}:{plan_key}"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "back:plans"}])
    return rows


def payment_keyboard(pending_id):
    return [
        [{"text": "✅ I've Paid — Check Now", "callback_data": f"check:{pending_id}"}],
    ]


def generate_unique_amount(base_usd, pending):
    """مبلغ رو به عدد صحیح (نزدیک‌ترین دلار) گرد می‌کنه و فقط چند سنت (زیر ۱ دلار، تا ۲ رقم
    اعشار) بهش اضافه می‌کنه تا منحصربه‌فرد بشه — این‌طوری مبلغ نهایی همیشه خیلی نزدیک به
    قیمت واقعی پلن می‌مونه و برای کاربر ابهامی ایجاد نمی‌کنه."""
    base = round(base_usd)
    used = {p["amount"] for p in pending}
    for cents in range(1, 100):  # 0.01 تا 0.99 دلار اضافه
        amount = round(base + cents / 100, 2)
        if amount not in used:
            return amount
    raise RuntimeError("Could not generate a unique payment amount")


# ================== پردازش پیام‌های عادی ==================

def handle_updates(state, pending, subscribers, discounts, applied, used_tx, referrals):
    offset = state.get("last_update_id", 0) + 1
    result = tg("getUpdates", offset=offset, timeout=LONG_POLL_SECONDS)

    for upd in result.get("result", []):
        state["last_update_id"] = upd["update_id"]

        if "callback_query" in upd:
            handle_callback(upd["callback_query"], pending, subscribers, discounts, applied, used_tx, referrals)
            continue

        msg = upd.get("message")
        if not msg or "text" not in msg:
            continue

        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg["text"].strip()

        if user_id in ADMIN_USER_IDS and text.startswith("/admin"):
            handle_admin_command(chat_id, text, subscribers, discounts)
            continue

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith("ref_"):
                try:
                    referrer_id = int(parts[1][4:])
                    already_sub = any(s["user_id"] == user_id for s in subscribers)
                    if referrer_id != user_id and str(user_id) not in referrals and not already_sub:
                        referrals[str(user_id)] = referrer_id
                except ValueError:
                    pass
            send_message(chat_id, WELCOME_TEXT, keyboard=main_menu_keyboard())

        elif text == "/help":
            send_message(chat_id, HELP_TEXT)

        elif text == "/refer":
            send_message(chat_id, referral_text(user_id))

        elif text == "/support":
            send_message(chat_id, support_text())

        elif text == "/disclaimer":
            send_message(chat_id, DISCLAIMER_TEXT)

        elif text == "/plans":
            active = next((s for s in subscribers if s["user_id"] == user_id), None)
            note = f"\n\n<i>Your current plan runs until {fmt_date(active['expiry_ts'])} — a new purchase extends it.</i>" if active else ""
            disc_note = ""
            uid_key = str(user_id)
            if uid_key in applied:
                disc_note = f"\n\n🏷 Discount <b>{applied[uid_key]['code']}</b> ({applied[uid_key]['percent']}% off) will be applied."
            send_message(chat_id, "💎 <b>Choose a plan</b>" + note + disc_note, keyboard=plans_keyboard())

        elif text.startswith("/code"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2:
                send_message(chat_id, "Usage: /code YOURCODE")
            else:
                d = find_discount(discounts, parts[1])
                if d:
                    applied[str(user_id)] = {"code": d["code"], "percent": d["percent"]}
                    send_message(chat_id, f"🏷 Code <b>{d['code']}</b> applied — {d['percent']}% off your next purchase.\nType /plans to continue.")
                else:
                    send_message(chat_id, "That code is invalid, expired, or fully used.")

        elif text == "/status":
            send_message(chat_id, format_status_message(subscribers, user_id))

        elif text == "/results":
            send_message(chat_id, format_results_message())

        elif text.startswith("/pnl"):
            parts = text.split()
            if len(parts) != 2:
                send_message(chat_id, "Usage: /pnl AMOUNT — e.g. /pnl 100")
            else:
                try:
                    amount = float(parts[1])
                    send_message(chat_id, format_pnl_message(amount))
                except ValueError:
                    send_message(chat_id, "Amount must be a number, e.g. /pnl 100")

        elif text == "/cancel":
            active = next((s for s in subscribers if s["user_id"] == user_id), None)
            if active:
                remove_member(user_id)
                subscribers.remove(active)
                send_message(chat_id, "Your membership has been cancelled and you've been removed from the channel.\nType /plans anytime to rejoin.")
            else:
                send_message(chat_id, "You don't have an active membership.")


def handle_callback(cb, pending, subscribers, discounts, applied, used_tx, referrals):
    data = cb["data"]
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    user_id = cb["from"]["id"]
    answer_callback(cb["id"])

    if data == "back:plans":
        edit_message(chat_id, message_id, "💎 <b>Choose a plan</b>", keyboard=plans_keyboard())
        return

    if data == "menu:home":
        edit_message(chat_id, message_id, WELCOME_TEXT, keyboard=main_menu_keyboard())
        return

    if data == "menu:plans":
        active = next((s for s in subscribers if s["user_id"] == user_id), None)
        note = f"\n\n<i>Your current plan runs until {fmt_date(active['expiry_ts'])} — a new purchase extends it.</i>" if active else ""
        edit_message(chat_id, message_id, "💎 <b>Choose a plan</b>" + note, keyboard=plans_keyboard())
        return

    if data == "menu:status":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, format_status_message(subscribers, user_id), keyboard=rows)
        return

    if data == "menu:results":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, format_results_message(), keyboard=rows)
        return

    if data == "menu:pnl":
        edit_message(chat_id, message_id, "🧮 Pick a risk amount per trade, or type <code>/pnl AMOUNT</code> for a custom value:", keyboard=pnl_keyboard())
        return

    if data == "menu:refer":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, referral_text(user_id), keyboard=rows)
        return

    if data == "menu:support":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, support_text(), keyboard=rows)
        return

    if data == "menu:disclaimer":
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:home"}]]
        edit_message(chat_id, message_id, DISCLAIMER_TEXT, keyboard=rows)
        return

    if data.startswith("pnl:"):
        amount = float(data.split(":")[1])
        rows = [[{"text": "⬅️ Back", "callback_data": "menu:pnl"}]]
        edit_message(chat_id, message_id, format_pnl_message(amount), keyboard=rows)
        return

    if data.startswith("plan:"):
        plan_key = data.split(":")[1]
        plan = PLANS[plan_key]
        price_line = f"${plan['usd']}"
        uid_key = str(user_id)
        if uid_key in applied:
            pct = applied[uid_key]["percent"]
            discounted = round(plan["usd"] * (1 - pct / 100))
            price_line = f"<s>${plan['usd']}</s> ${discounted} (-{pct}%)"
        edit_message(chat_id, message_id, f"💎 <b>{plan['label']}</b> — {price_line}\n\nChoose a payment method:", keyboard=networks_keyboard(plan_key))
        return

    if data.startswith("net:"):
        _, net_key, plan_key = data.split(":")
        plan = PLANS[plan_key]
        net = NETWORKS[net_key]

        base_usd = plan["usd"]
        uid_key = str(user_id)
        applied_code = None
        if uid_key in applied:
            pct = applied[uid_key]["percent"]
            applied_code = applied[uid_key]["code"]
            base_usd = round(base_usd * (1 - pct / 100), 2)

        pending[:] = [p for p in pending if p["user_id"] != user_id]

        amount = generate_unique_amount(base_usd, pending)
        pending_id = f"{user_id}-{now_ts()}"
        pending.append({
            "id": pending_id, "user_id": user_id, "chat_id": chat_id, "plan": plan_key, "network": net_key,
            "amount": amount, "created_at": now_ts(), "expires_at": now_ts() + PAYMENT_WINDOW_MINUTES * 60,
            "discount_code": applied_code,
        })
        if uid_key in applied:
            del applied[uid_key]

        edit_message(
            chat_id, message_id,
            f"🔐 <b>Send payment</b>\n\n"
            f"Plan: {plan['label']}\n"
            f"Network: {net['label']}\n\n"
            f"Amount: <b>{amount} USDT</b>\n"
            f"Address:\n<code>{net['wallet']}</code>\n\n"
            f"⚠️ Send the <b>exact amount</b> shown (a few cents above the plan "
            f"price are added automatically so your payment is identified instantly).\n"
            f"⚠️ {net['label']} network only.\n"
            f"⏳ Valid for {PAYMENT_WINDOW_MINUTES} minutes.\n\n"
            f"Paid already? Tap the button below to check instantly.",
            keyboard=payment_keyboard(pending_id),
        )
        return

    if data.startswith("check:"):
        pending_id = data.split(":", 1)[1]
        p = next((x for x in pending if x["id"] == pending_id), None)
        if not p:
            answer_callback(cb["id"], "This payment request is no longer active.")
            return
        transfers = fetch_all_transfers()
        confirmed = try_confirm_payment(p, transfers, subscribers, discounts, used_tx, referrals)
        if confirmed:
            pending.remove(p)
        else:
            send_message(chat_id, "⏳ No matching payment found yet — this can take a minute or two after you send it. Try again shortly.")


# ================== تایید پرداخت (مشترک بین چک دستی و چک دوره‌ای) ==================

def try_confirm_payment(p, transfers, subscribers, discounts, used_tx, referrals) -> bool:
    net = NETWORKS[p["network"]]
    for tx in transfers:
        if tx["network"] != p["network"] or tx["tx_id"] in used_tx:
            continue
        if tx["to"] != net["wallet"].lower():
            continue
        value_usdt = units_to_usdt(tx["value"], net["decimals"])
        if abs(value_usdt - p["amount"]) < 0.005 and tx["ts"] >= p["created_at"] - 60:
            used_tx.append(tx["tx_id"])
            plan = PLANS[p["plan"]]
            existing = next((s for s in subscribers if s["user_id"] == p["user_id"]), None)
            is_first_ever_purchase = existing is None
            base_ts = existing["expiry_ts"] if existing and existing["expiry_ts"] > now_ts() else now_ts()
            new_expiry = base_ts + plan["days"] * 24 * 3600

            if existing:
                existing["expiry_ts"] = new_expiry
                existing["reminded"] = False
            else:
                subscribers.append({"user_id": p["user_id"], "chat_id": p["chat_id"], "expiry_ts": new_expiry, "reminded": False})

            if p.get("discount_code"):
                d = next((x for x in discounts if x["code"] == p["discount_code"]), None)
                if d:
                    d["used"] = d.get("used", 0) + 1

            # اگه این اولین خرید کاربر بوده و از طریق لینک رفرال اومده، پاداش رو به معرف بده
            if is_first_ever_purchase:
                referrer_id = referrals.pop(str(p["user_id"]), None)
                if referrer_id:
                    ref_sub = next((s for s in subscribers if s["user_id"] == referrer_id), None)
                    ref_base = ref_sub["expiry_ts"] if ref_sub and ref_sub["expiry_ts"] > now_ts() else now_ts()
                    ref_new_expiry = ref_base + REFERRAL_REWARD_DAYS * 24 * 3600
                    if ref_sub:
                        ref_sub["expiry_ts"] = ref_new_expiry
                    else:
                        subscribers.append({"user_id": referrer_id, "chat_id": referrer_id, "expiry_ts": ref_new_expiry, "reminded": False})
                    send_message(referrer_id, f"🎉 A friend you referred just subscribed! You've earned <b>{REFERRAL_REWARD_DAYS} free days</b>.\nNew expiry: {fmt_date(ref_new_expiry)}")

            try:
                link = create_invite_link(expire_minutes=60)
                extra = "" if not existing else " (renewed)"
                send_message(p["chat_id"], f"✅ <b>Payment confirmed!</b>{extra}\nExpires: {fmt_date(new_expiry)}\n\nJoin here (one-time link, 1 hour):\n{link}")
            except Exception as e:
                send_message(p["chat_id"], "✅ Payment confirmed, but we couldn't create your invite link automatically. Message us and we'll add you.")
                print(f"[error] invite link failed: {e}")
            return True
    return False


def process_pending_payments(pending, subscribers, discounts, used_tx, referrals):
    if not pending:
        return
    transfers = fetch_all_transfers()
    still_pending = []
    for p in pending:
        if now_ts() > p["expires_at"]:
            send_message(p["chat_id"], "⌛ This payment window expired. Type /plans to try again.")
            continue
        if try_confirm_payment(p, transfers, subscribers, discounts, used_tx, referrals):
            continue
        still_pending.append(p)
    pending[:] = still_pending


def process_subscription_lifecycle(subscribers):
    still_active = []
    for s in subscribers:
        remaining = s["expiry_ts"] - now_ts()
        if remaining <= 0:
            remove_member(s["user_id"])
            send_message(s["chat_id"], "⌛ Your subscription has ended and you've been removed from the channel.\nType /plans to renew.")
            continue
        if remaining <= 3 * 24 * 3600 and not s.get("reminded"):
            send_message(s["chat_id"], f"🔔 Your subscription ends {fmt_date(s['expiry_ts'])}. Type /plans to renew.")
            s["reminded"] = True
        still_active.append(s)
    subscribers[:] = still_active


# ================== دستورات ادمین ==================

ADMIN_HELP = (
    "<b>Admin commands</b>\n\n"
    "<u>Users</u>\n"
    "/admin_list — list active subscribers\n"
    "/admin_add &lt;user_id&gt; &lt;days&gt; — add/extend a user\n"
    "/admin_extend &lt;user_id&gt; &lt;days&gt; — add N days\n"
    "/admin_reduce &lt;user_id&gt; &lt;days&gt; — subtract N days\n"
    "/admin_remove &lt;user_id&gt; — remove immediately\n\n"
    "<u>Discounts</u>\n"
    "/admin_discount_add &lt;CODE&gt; &lt;percent&gt; [max_uses] [days_valid]\n"
    "/admin_discount_list\n"
    "/admin_discount_remove &lt;CODE&gt;"
)


def _find_sub(subscribers, user_id):
    return next((s for s in subscribers if s["user_id"] == user_id), None)


def handle_admin_command(chat_id, text, subscribers, discounts):
    parts = text.split()
    cmd = parts[0]

    if cmd in ("/admin_list", "/admin"):
        if not subscribers:
            send_message(chat_id, "No active subscribers.\n\n" + ADMIN_HELP)
        else:
            lines = [f"<code>{s['user_id']}</code> — until {fmt_date(s['expiry_ts'])}" for s in subscribers]
            send_message(chat_id, "<b>Active subscribers:</b>\n" + "\n".join(lines) + "\n\n" + ADMIN_HELP)
        return

    if cmd == "/admin_help":
        send_message(chat_id, ADMIN_HELP)
        return

    if cmd in ("/admin_add", "/admin_extend"):
        if len(parts) != 3:
            send_message(chat_id, f"Usage: {cmd} <user_id> <days>")
            return
        try:
            target_id, days = int(parts[1]), int(parts[2])
        except ValueError:
            send_message(chat_id, "user_id and days must be numbers.")
            return
        sub = _find_sub(subscribers, target_id)
        base = sub["expiry_ts"] if sub and sub["expiry_ts"] > now_ts() else now_ts()
        new_expiry = base + days * 24 * 3600
        if sub:
            sub["expiry_ts"] = new_expiry
            sub["reminded"] = False
        else:
            subscribers.append({"user_id": target_id, "chat_id": target_id, "expiry_ts": new_expiry, "reminded": False})
        send_message(chat_id, f"Done. User {target_id} active until {fmt_date(new_expiry)}.")
        send_message(target_id, f"🎁 Your subscription was updated by an admin.\nNew expiry: {fmt_date(new_expiry)}")
        return

    if cmd == "/admin_reduce":
        if len(parts) != 3:
            send_message(chat_id, "Usage: /admin_reduce <user_id> <days>")
            return
        try:
            target_id, days = int(parts[1]), int(parts[2])
        except ValueError:
            send_message(chat_id, "user_id and days must be numbers.")
            return
        sub = _find_sub(subscribers, target_id)
        if not sub:
            send_message(chat_id, "That user has no active subscription.")
            return
        sub["expiry_ts"] -= days * 24 * 3600
        if sub["expiry_ts"] <= now_ts():
            remove_member(target_id)
            subscribers.remove(sub)
            send_message(chat_id, f"User {target_id}'s subscription reached zero and was removed.")
            send_message(target_id, "Your subscription has ended. Type /plans to resubscribe.")
        else:
            send_message(chat_id, f"Done. User {target_id} now active until {fmt_date(sub['expiry_ts'])}.")
            send_message(target_id, f"Your subscription was adjusted by an admin.\nNew expiry: {fmt_date(sub['expiry_ts'])}")
        return

    if cmd == "/admin_remove":
        if len(parts) != 2:
            send_message(chat_id, "Usage: /admin_remove <user_id>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            send_message(chat_id, "user_id must be a number.")
            return
        sub = _find_sub(subscribers, target_id)
        remove_member(target_id)
        if sub:
            subscribers.remove(sub)
        send_message(chat_id, f"User {target_id} removed.")
        send_message(target_id, "Your membership has been removed by an admin.")
        return

    if cmd == "/admin_discount_add":
        if len(parts) < 3:
            send_message(chat_id, "Usage: /admin_discount_add <CODE> <percent> [max_uses] [days_valid]")
            return
        code = parts[1].upper()
        try:
            percent = float(parts[2])
            max_uses = int(parts[3]) if len(parts) > 3 else None
            days_valid = int(parts[4]) if len(parts) > 4 else None
        except ValueError:
            send_message(chat_id, "percent/max_uses/days_valid must be numbers.")
            return
        discounts[:] = [d for d in discounts if d["code"] != code]
        discounts.append({
            "code": code, "percent": percent, "max_uses": max_uses, "used": 0,
            "expires_at": (now_ts() + days_valid * 24 * 3600) if days_valid else None,
            "active": True,
        })
        send_message(chat_id, f"Discount <b>{code}</b> created: {percent}% off" +
                     (f", max {max_uses} uses" if max_uses else "") +
                     (f", valid {days_valid} days" if days_valid else ", no expiry") + ".")
        return

    if cmd == "/admin_discount_list":
        if not discounts:
            send_message(chat_id, "No discount codes.")
            return
        lines = []
        for d in discounts:
            exp = fmt_date(d["expires_at"]) if d.get("expires_at") else "never"
            uses = f"{d.get('used', 0)}/{d['max_uses']}" if d.get("max_uses") else f"{d.get('used', 0)}/∞"
            lines.append(f"<b>{d['code']}</b> — {d['percent']}% off, used {uses}, expires {exp}, active={d.get('active', True)}")
        send_message(chat_id, "\n".join(lines))
        return

    if cmd == "/admin_discount_remove":
        if len(parts) != 2:
            send_message(chat_id, "Usage: /admin_discount_remove <CODE>")
            return
        code = parts[1].upper()
        before = len(discounts)
        discounts[:] = [d for d in discounts if d["code"] != code]
        send_message(chat_id, f"Removed {code}." if len(discounts) < before else f"No such code: {code}")
        return

    send_message(chat_id, "Unknown admin command.\n\n" + ADMIN_HELP)


# ================== main ==================

def git_commit_and_push():
    """هر چند دقیقه یک‌بار تغییرات data/ رو کامیت و پوش می‌کنه تا در طول اجرای طولانی
    هیچ پرداخت یا مشترکی گم نشه، حتی اگه جاب به هر دلیلی وسط راه قطع بشه."""
    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["git", "add", "data/"], cwd=repo_dir, check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir)
        if result.returncode == 0:
            return  # چیزی تغییر نکرده
        subprocess.run(["git", "commit", "-m", "update subscription data [skip ci]"], cwd=repo_dir, check=True)
        subprocess.run(["git", "pull", "--rebase"], cwd=repo_dir, check=False)
        subprocess.run(["git", "push"], cwd=repo_dir, check=True)
        print("[git] committed & pushed data/ changes")
    except Exception as e:
        print(f"[warn] git_commit_and_push failed: {e}")


def run_cycle(state, pending, subscribers, discounts, applied, used_tx, referrals):
    handle_updates(state, pending, subscribers, discounts, applied, used_tx, referrals)
    process_pending_payments(pending, subscribers, discounts, used_tx, referrals)
    process_subscription_lifecycle(subscribers)
    used_tx[:] = used_tx[-800:]


def save_all(state, pending, subscribers, discounts, applied, used_tx, referrals):
    _save("state.json", state)
    _save("pending.json", pending)
    _save("subscribers.json", subscribers)
    _save("discounts.json", discounts)
    _save("applied_discounts.json", applied)
    _save("used_tx.json", used_tx)
    _save("referrals.json", referrals)


def main():
    state = _load("state.json", {"last_update_id": 0})
    pending = _load("pending.json", [])
    subscribers = _load("subscribers.json", [])
    discounts = _load("discounts.json", [])
    applied = _load("applied_discounts.json", {})
    used_tx = _load("used_tx.json", [])
    referrals = _load("referrals.json", {})

    start = time.time()
    last_commit = start
    cycles = 0

    print(f"🚀 Starting continuous loop — will run for up to {LOOP_MAX_SECONDS/3600:.1f}h, "
          f"long-polling every {LONG_POLL_SECONDS}s for instant responses.")

    while time.time() - start < LOOP_MAX_SECONDS:
        try:
            run_cycle(state, pending, subscribers, discounts, applied, used_tx, referrals)
        except Exception as e:
            print(f"[error] run_cycle failed: {e}")
            time.sleep(5)  # جلوگیری از حلقه‌ی خطای سریع در صورت مشکل موقت شبکه

        cycles += 1
        if time.time() - last_commit >= GIT_COMMIT_EVERY_SECONDS:
            save_all(state, pending, subscribers, discounts, applied, used_tx, referrals)
            git_commit_and_push()
            last_commit = time.time()

    save_all(state, pending, subscribers, discounts, applied, used_tx, referrals)
    git_commit_and_push()
    print(f"✅ Loop finished after {cycles} cycles, {(time.time()-start)/3600:.2f}h — "
          f"pending: {len(pending)}, subscribers: {len(subscribers)}")


if __name__ == "__main__":
    main()
