import re
import json
import time
import os
import requests

from config import (
    TELEGRAM_API_URL,
    CHANNEL_PROGNOZ,
    CHANNEL_STATS,
    OFFSET_FILE,
    FINALIZE_WAIT_SECONDS,
)

# =====================================================================
# HTTP-СЕССИЯ (защищена от зависаний)
# =====================================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Connection": "close",  # важно: не держим протухшие соединения
})

# =====================================================================
# OFFSET
# =====================================================================

def load_offset():
    try:
        if OFFSET_FILE and os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r", encoding="utf-8") as f:
                value = f.read().strip()
                if value:
                    return int(value)
    except Exception:
        pass
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ Ошибка сохранения offset: {e}", flush=True)


# =====================================================================
# WEBHOOK
# =====================================================================

def delete_webhook():
    try:
        response = SESSION.post(
            f"{TELEGRAM_API_URL}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
        data = response.json()
        if data.get("ok"):
            print("✅ Webhook удалён, используем getUpdates", flush=True)
            return True
        print(f"❌ Ошибка deleteWebhook: {data}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка удаления webhook: {e}", flush=True)
    return False


# =====================================================================
# ОТПРАВКА СООБЩЕНИЙ
# =====================================================================

def telegram_send(text, chat_id=None):
    target_chat = chat_id or CHANNEL_PROGNOZ
    try:
        response = SESSION.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": target_chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        data = response.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"❌ Telegram sendMessage: {data}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки Telegram: {e}", flush=True)
    return None


def telegram_edit(message_id, text, chat_id=None):
    if not message_id:
        return False
    target_chat = chat_id or CHANNEL_PROGNOZ
    try:
        response = SESSION.post(
            f"{TELEGRAM_API_URL}/editMessageText",
            json={
                "chat_id": target_chat,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        data = response.json()
        return bool(data.get("ok"))
    except Exception as e:
        print(f"⚠️ Ошибка редактирования Telegram: {e}", flush=True)
    return False


# =====================================================================
# ПРИЁМ АПДЕЙТОВ
# =====================================================================

def process_telegram_updates(offset, on_game_message):
    try:
        response = SESSION.get(
            f"{TELEGRAM_API_URL}/getUpdates",
            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,
                "allowed_updates": json.dumps([
                    "channel_post",
                    "edited_channel_post",
                ]),
            },
            timeout=10,
        )
        data = response.json()
        if not data.get("ok"):
            # Обработка 429 (слишком много запросов)
            if data.get("error_code") == 429:
                retry_after = data.get("parameters", {}).get("retry_after", 5)
                print(f"⏳ Telegram 429: ждём {retry_after} сек", flush=True)
                time.sleep(retry_after)
            else:
                print(f"❌ Telegram getUpdates: {data}", flush=True)
            return offset

        updates = data.get("result", [])
        for update in updates:
            update_id = update.get("update_id")
            if update_id is not None:
                offset = update_id + 1
                save_offset(offset)

            post = update.get("channel_post") or update.get("edited_channel_post")
            if not post:
                continue

            chat = post.get("chat", {})
            chat_id = str(chat.get("id", ""))
            text = post.get("text", "")

            if chat_id != str(CHANNEL_STATS):
                continue
            if not text:
                continue

            number_match = re.search(r"#N(\d+)", text)
            if not number_match:
                continue

            game_number = int(number_match.group(1))
            is_edited = "edited_channel_post" in update

            try:
                on_game_message(game_number, text, is_edited)
            except Exception as e:
                print(f"⚠️ Ошибка обработки игры #N{game_number}: {e}", flush=True)

    except Exception as e:
        print(f"⚠️ Updates error: {e}", flush=True)

    return offset