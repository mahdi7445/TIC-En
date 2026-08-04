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
import requests
from datetime import datetime, timezone

# ================== تنظیمات پایه ==================
BOT_TOKEN = os.environ["BOT_TOKEN"]
PRIVATE_CHANNEL_ID = os.environ["PRIVATE_CHANNEL_ID"]
PUBLIC_CHANNEL_LINK = os.environ.get("PUBLIC_CHANNEL_LINK", "")
ADMIN_USER_IDS = {int(x) for x in os.environ.get("ADMIN_USER_IDS", "").replace(" ", "").split(",") if x}

WALLET_TRC20 = os.environ["WALLET_ADDRESS_TRC20"]
WALLET_BEP20 = os.environ.get("WALLET_ADDRESS_BEP20", "")
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY", "")

PAYMENT_WINDOW_MINUTES = int(os.environ.get("PAYMENT_WINDOW_MINUTES") or "45")

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
    "Every call includes entry, stop-loss, 3 profit targets, and a chart.\n\n"
    "✅ Every signal generated gets posted — wins and losses alike\n"
    f"{('👀 Free samples: ' + PUBLIC_CHANNEL_LINK) if PUBLIC_CHANNEL_LINK else ''}\n\n"
    "Type /plans to subscribe."
)

HELP_TEXT = (
    "<b>Commands</b>\n\n"
    "/plans — view plans &amp; subscribe\n"
    "/status — your subscription status\n"
    "/code CODE — apply a discount code\n"
    "/cancel — cancel your membership\n"
    "/help — this message"
)

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


# ================== توابع تلگرام ==================

def tg(method, **params):
    resp = requests.post(f"{API}/{method}", json=params, timeout=30)
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
    used = {p["amount"] for p in pending}
    for _ in range(500):
        offset = random.randint(1, 9999) / 10000
        amount = round(base_usd + offset, 4)
        if amount not in used:
            return amount
    raise RuntimeError("Could not generate a unique payment amount")


# ================== پردازش پیام‌های عادی ==================

def handle_updates(state, pending, subscribers, discounts, applied, used_tx):
    offset = state.get("last_update_id", 0) + 1
    result = tg("getUpdates", offset=offset, timeout=0)

    for upd in result.get("result", []):
        state["last_update_id"] = upd["update_id"]

        if "callback_query" in upd:
            handle_callback(upd["callback_query"], pending, subscribers, discounts, applied, used_tx)
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

        if text in ("/start", "/help"):
            send_message(chat_id, WELCOME_TEXT if text == "/start" else HELP_TEXT)

        elif text == "/plans":
            active = next((s for s in subscribers if s["user_id"] == user_id), None)
            note = f"\n\n<i>Your current plan runs until {fmt_date(active['expiry_ts'])} — a new purchase extends it.</i>" if active else ""
            disc_note = ""
            if user_id in applied:
                disc_note = f"\n\n🏷 Discount <b>{applied[user_id]['code']}</b> ({applied[user_id]['percent']}% off) will be applied."
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
            active = next((s for s in subscribers if s["user_id"] == user_id), None)
            if active:
                send_message(chat_id, f"✅ <b>Active</b>\nExpires: {fmt_date(active['expiry_ts'])}")
            else:
                send_message(chat_id, "❌ No active subscription.\nType /plans to subscribe.")

        elif text == "/cancel":
            active = next((s for s in subscribers if s["user_id"] == user_id), None)
            if active:
                remove_member(user_id)
                subscribers.remove(active)
                send_message(chat_id, "Your membership has been cancelled and you've been removed from the channel.\nType /plans anytime to rejoin.")
            else:
                send_message(chat_id, "You don't have an active membership.")


def handle_callback(cb, pending, subscribers, discounts, applied, used_tx):
    data = cb["data"]
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    user_id = cb["from"]["id"]
    answer_callback(cb["id"])

    if data == "back:plans":
        edit_message(chat_id, message_id, "💎 <b>Choose a plan</b>", keyboard=plans_keyboard())
        return

    if data.startswith("plan:"):
        plan_key = data.split(":")[1]
        plan = PLANS[plan_key]
        price_line = f"${plan['usd']}"
        uid_key = str(user_id)
        if uid_key in applied:
            pct = applied[uid_key]["percent"]
            discounted = round(plan["usd"] * (1 - pct / 100), 2)
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
            f"⚠️ Send the <b>exact</b> amount shown — this is how payments are matched automatically.\n"
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
        confirmed = try_confirm_payment(p, transfers, subscribers, discounts, used_tx)
        if confirmed:
            pending.remove(p)
        else:
            send_message(chat_id, "⏳ No matching payment found yet — this can take a minute or two after you send it. Try again shortly.")


# ================== تایید پرداخت (مشترک بین چک دستی و چک دوره‌ای) ==================

def try_confirm_payment(p, transfers, subscribers, discounts, used_tx) -> bool:
    net = NETWORKS[p["network"]]
    for tx in transfers:
        if tx["network"] != p["network"] or tx["tx_id"] in used_tx:
            continue
        if tx["to"] != net["wallet"].lower():
            continue
        value_usdt = units_to_usdt(tx["value"], net["decimals"])
        if abs(value_usdt - p["amount"]) < 0.00005 and tx["ts"] >= p["created_at"] - 60:
            used_tx.append(tx["tx_id"])
            plan = PLANS[p["plan"]]
            existing = next((s for s in subscribers if s["user_id"] == p["user_id"]), None)
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

            try:
                link = create_invite_link(expire_minutes=60)
                extra = "" if not existing else " (renewed)"
                send_message(p["chat_id"], f"✅ <b>Payment confirmed!</b>{extra}\nExpires: {fmt_date(new_expiry)}\n\nJoin here (one-time link, 1 hour):\n{link}")
            except Exception as e:
                send_message(p["chat_id"], "✅ Payment confirmed, but we couldn't create your invite link automatically. Message us and we'll add you.")
                print(f"[error] invite link failed: {e}")
            return True
    return False


def process_pending_payments(pending, subscribers, discounts, used_tx):
    if not pending:
        return
    transfers = fetch_all_transfers()
    still_pending = []
    for p in pending:
        if now_ts() > p["expires_at"]:
            send_message(p["chat_id"], "⌛ This payment window expired. Type /plans to try again.")
            continue
        if try_confirm_payment(p, transfers, subscribers, discounts, used_tx):
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

def main():
    state = _load("state.json", {"last_update_id": 0})
    pending = _load("pending.json", [])
    subscribers = _load("subscribers.json", [])
    discounts = _load("discounts.json", [])
    applied = _load("applied_discounts.json", {})
    used_tx = _load("used_tx.json", [])

    handle_updates(state, pending, subscribers, discounts, applied, used_tx)
    process_pending_payments(pending, subscribers, discounts, used_tx)
    process_subscription_lifecycle(subscribers)

    used_tx[:] = used_tx[-800:]

    _save("state.json", state)
    _save("pending.json", pending)
    _save("subscribers.json", subscribers)
    _save("discounts.json", discounts)
    _save("applied_discounts.json", applied)
    _save("used_tx.json", used_tx)

    print(f"OK - pending: {len(pending)}, subscribers: {len(subscribers)}, discounts: {len(discounts)}")


if __name__ == "__main__":
    main()
