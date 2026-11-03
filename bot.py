import os
import sys
import re
import json
import time
import math

from datetime import datetime, time as dtime, timedelta

from config import (
    BOT_TOKEN,
    CHANNEL_PROGNOZ,
    CHANNEL_STATS,
    PREDICTIONS_FILE,
    POLL_INTERVAL,
    DOGON_GAMES,
    CLEANUP_HOUR,
    CLEANUP_MINUTE,
    MAX_GAMES_CACHE,
    MAX_PREDICTIONS_STORED,
    MOSCOW_TZ,
)

from parsers import (
    parse_game_message,
    log_game,
    find_new_trigger,
    calculate_prediction,
    check_prediction_result,
)

from coefs import get_player_cf

from telegram_api import (
    delete_webhook,
    telegram_send,
    telegram_edit,
    telegram_delete,
    process_telegram_updates,
    load_offset,
)

from bank import (
    load_bank,
    save_bank,
    get_current_bet,
    get_balance,
    apply_dogon_bet,
    apply_win,
    apply_lose,
    apply_return,
)

# =====================================================================
# КОНСТАНТЫ
# =====================================================================

FINALIZE_WAIT_SECONDS = 30
PREDICTION_TIMEOUT_MINUTES = 300  # Таймаут прогноза (5 часов)
BASE_BET = 50.0  # Базовая ставка

# =====================================================================
# ПРОВЕРКА ENV
# =====================================================================

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True)
    sys.exit(1)

if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан", flush=True)
    sys.exit(1)


# =====================================================================
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# =====================================================================

games_cache = {}
finalized_games = {}
pending_games = {}
predictions = []
last_cleanup_date = None


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================

def now_moscow():
    return datetime.now(MOSCOW_TZ)


def parse_datetime(value, default=None):
    if not value:
        return default
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            result = MOSCOW_TZ.localize(result)
        return result
    except (ValueError, TypeError):
        return default


def get_game(game_number):
    """Возвращает наиболее актуальную версию игры."""
    return games_cache.get(game_number) or finalized_games.get(game_number)


def normalize_message_id(response):
    """Извлекает message_id из поддерживаемых форматов ответа."""
    if isinstance(response, bool):
        return None
    if isinstance(response, int):
        return response if response > 0 else None
    if not isinstance(response, dict):
        return None
    if response.get("message_id"):
        return response["message_id"]
    result = response.get("result")
    if isinstance(result, dict):
        message_id = result.get("message_id")
        if message_id:
            return message_id
    return None


def save_state():
    save_predictions()
    save_bank()


# =====================================================================
# ОЧИСТКА
# =====================================================================

def should_cleanup_now(now=None):
    global last_cleanup_date
    if now is None:
        now = now_moscow()
    if last_cleanup_date == now.date():
        return False
    cleanup_time = dtime(CLEANUP_HOUR, CLEANUP_MINUTE)
    return now.time() >= cleanup_time


def cleanup_nightly():
    global games_cache, finalized_games, pending_games, last_cleanup_date
    now = now_moscow()
    print("🧹 НОЧНАЯ ОЧИСТКА", flush=True)
    games_cache.clear()
    finalized_games.clear()
    pending_games.clear()
    last_cleanup_date = now.date()


# =====================================================================
# ХРАНЕНИЕ ПРОГНОЗОВ
# =====================================================================

def load_predictions():
    global predictions
    try:
        if not os.path.exists(PREDICTIONS_FILE):
            predictions = []
            return
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        predictions = data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ Ошибка загрузки прогнозов: {e}", flush=True)
        predictions = []


def save_predictions():
    try:
        directory = os.path.dirname(os.path.abspath(PREDICTIONS_FILE))
        os.makedirs(directory, exist_ok=True)
        tmp = PREDICTIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PREDICTIONS_FILE)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


def has_active_prediction():
    active_statuses = ("pending_check", "pending_publish", "pending")
    return any(p.get("status") in active_statuses for p in predictions)


# =====================================================================
# СООБЩЕНИЯ TELEGRAM
# =====================================================================

def make_prediction_message(prediction):
    target_games = prediction["target_games"]
    rank = prediction["predicted_rank"]
    suits = " или ".join(prediction["predicted_suits"])
    bet = prediction.get("bet", BASE_BET)
    
    games_str = ", ".join(f"#{g}" for g in target_games)
    
    return (
        f"🎯 Триггер #{prediction['trigger_number']}: "
        f"Игры: <b>{games_str}</b>\n"
        f"Ждем: <b>{rank}{suits}</b> | Ставка: {bet}₽"
    )


def make_result_message(prediction, result, found_card=None):
    target_games = prediction["target_games"]
    rank = prediction["predicted_rank"]
    bet = prediction.get("bet", BASE_BET)
    balance = int(math.ceil(get_balance()))
    games_str = ", ".join(f"#{g}" for g in target_games)

    if result == "win":
        return (
            f"🎯 Игры: <b>{games_str}</b>: {rank} ✅ (карта: {found_card})\n"
            f"🤑 Баланс: {balance}₽"
        )
    if result == "lose":
        return (
            f"🎯 Игры: <b>{games_str}</b>: {rank} ❌ (не зашла)\n"
            f"🤑 Баланс: {balance}₽"
        )
    return f"🎯 Игры: <b>{games_str}</b>: {rank} ⚠️"


def is_waiting_message(text):
    if not text:
        return True
    if "Ожидание" in text or "⏳" in text:
        return True
    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2:
        return True
    card_pattern = re.compile(r"(?:10|[2-9AJQK])[♠♣♦♥]")
    if not card_pattern.search(groups[0]):
        return True
    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(game):
    if has_active_prediction():
        return None

    trigger = find_new_trigger(game)
    if not trigger:
        return None

    trigger_number = game.get("game_number")
    if trigger_number is None:
        return None
    try:
        trigger_number = int(trigger_number)
    except (ValueError, TypeError):
        return None

    before_number = trigger_number - 1
    if before_number < 1:
        before_number = 1440 
    before_game = get_game(before_number)
    
    if before_game and before_game.get("is_r"):
        print(f"⛔ #N{trigger_number}: #R перед триггером, пропуск", flush=True)
        return None

    prediction_data = calculate_prediction(trigger)

    prediction = {
        "algorithm": "new_logic_v1",
        "trigger_number": trigger_number,
        "trigger_game_id": game.get("game_id"),
        "trigger_card": trigger.get("trigger_card_text"),
        "predicted_rank": prediction_data["predicted_rank"],
        "predicted_suits": prediction_data["predicted_suits"],
        "target_games": prediction_data["target_games"],
        
        "base_bet": BASE_BET,
        "bet": BASE_BET,
        "status": "pending_publish", # <--- ИСПРАВЛЕНО: теперь сразу на отправку
        
        "created_at": now_moscow().isoformat(),
        "sent_at": None,
        "closed_at": None,
        "message_id": None,
        "pending_publish": True, # <--- Флаг для отправки
        "result_game": None,
        "found_card": None,
        "last_publish_attempt": None,
    }

    predictions.append(prediction)
    save_predictions()

    print(f"🔮 СОЗДАН #N{trigger_number} -> Цель: {prediction['target_games']}", flush=True)
    return prediction


# =====================================================================
# ПУБЛИКАЦИЯ ПРОГНОЗА
# =====================================================================

def send_prediction(prediction):
    if prediction.get("message_id"):
        return True

    message = make_prediction_message(prediction)
    print(f"DEBUG: Пытаюсь отправить сообщение: {message}", flush=True) # <--- ДЕБАГ

    prediction["status"] = "pending_publish"
    prediction["pending_publish"] = True
    prediction["last_publish_attempt"] = now_moscow().isoformat()

    save_predictions()

    try:
        print("DEBUG: Вызываю telegram_send...", flush=True) # <--- ДЕБАГ
        response = telegram_send(message)
        print(f"DEBUG: Ответ от Telegram: {response}", flush=True) # <--- ДЕБАГ
        message_id = normalize_message_id(response)
    except Exception as e:
        print(f"⚠️ Ошибка публикации #{prediction['trigger_number']}: {e}", flush=True)
        save_predictions()
        return False

    if not message_id:
        prediction["status"] = "pending_publish"
        prediction["pending_publish"] = True
        prediction["message_id"] = None
        save_predictions()
        print(f"⚠️ Telegram не вернул message_id для #{prediction['trigger_number']}", flush=True)
        return False

    prediction["message_id"] = message_id
    prediction["sent_at"] = now_moscow().isoformat()
    prediction["status"] = "pending"
    prediction["pending_publish"] = False

    apply_dogon_bet(prediction, 0)

    save_state()
    print(f"📤 ОТПРАВЛЕН прогноз на {prediction['target_games']} (ставка {prediction['bet']}₽)", flush=True)
    return True


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ (ЗАШЛА ЛИ КАРТА)
# =====================================================================

def check_predictions():
    changed = False
    now = now_moscow()

    for p in predictions:
        if p.get("status") != "pending":
            continue

        target_games = p.get("target_games", [])
        last_game = target_games[-1] if target_games else 0
        
        for game_num in target_games:
            game = finalized_games.get(game_num)
            if not game:
                continue
            
            found_card = check_prediction_result(game, p)
            
            if found_card:
                p["status"] = "win"
                p["found_card"] = found_card
                p["result_game"] = game_num
                p["closed_at"] = now.isoformat()
                
                apply_win(p, 0, found_card)
                
                if p.get("message_id"):
                    telegram_edit(p["message_id"], make_result_message(p, "win", found_card))
                
                print(f"✅ ПРОГНОЗ #{p['trigger_number']} ЗАШЕЛ в игре #{game_num}! Карта: {found_card}", flush=True)
                changed = True
                break

        if p.get("status") == "pending":
            # Если последняя игра из пула уже завершилась, а карты нет — проигрыш
            final_game_data = finalized_games.get(last_game)
            if final_game_data:
                p["status"] = "lose"
                p["closed_at"] = now.isoformat()
                p["result_game"] = last_game
                
                apply_lose(p, 0)
                
                if p.get("message_id"):
                    telegram_edit(p["message_id"], make_result_message(p, "lose"))
                
                print(f"❌ ПРОГНОЗ #{p['trigger_number']} ПРОИГРАН (игры {target_games} прошли)", flush=True)
                changed = True
                continue
                
            # Проверка по времени
            sent_at = parse_datetime(p.get("sent_at"), now)
            if sent_at and now - sent_at > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES):
                 p["status"] = "return"
                 p["closed_at"] = now.isoformat()
                 apply_return(p, p.get("bet", 0))
                 if p.get("message_id"):
                    telegram_edit(p["message_id"], make_result_message(p, "return"))
                 print(f"♻️ Возврат #{p['trigger_number']}: таймаут", flush=True)
                 changed = True

    if changed:
        save_state()


# =====================================================================
# ОЖИДАЮЩИЕ ПУБЛИКАЦИИ
# =====================================================================

def resolve_pending_publishes():
    for p in predictions:
        if p.get("status") == "pending_publish" and not p.get("message_id"):
            send_prediction(p)


# =====================================================================
# ФИНАЛИЗАЦИЯ ИГР
# =====================================================================

def finalize_pending_games():
    current_time = time.time()
    ready = [
        num for num, info in pending_games.items()
        if current_time - info.get("last_update", current_time) >= FINALIZE_WAIT_SECONDS
    ]
    for num in ready:
        info = pending_games.pop(num, None)
        if not info:
            continue
        game = parse_game_message(info.get("text", ""))
        if game:
            finalized_games[num] = game


# =====================================================================
# ПОЛУЧЕНИЕ ИГРОВЫХ СООБЩЕНИЙ
# =====================================================================

def on_game_message(game_number, text, is_edited):
    if is_waiting_message(text):
        return

    game = parse_game_message(text)
    if not game or not game.get("player_cards"):
        return

    games_cache[game_number] = game
    pending_games[game_number] = {
        "text": text,
        "last_update": time.time()
    }

    log_game(game)
    create_prediction(game)


# =====================================================================
# ОЧИСТКА КЭША
# =====================================================================

def cleanup_caches():
    if len(games_cache) > MAX_GAMES_CACHE:
        oldest = sorted(games_cache.keys())[:len(games_cache) - MAX_GAMES_CACHE]
        for num in oldest:
            del games_cache[num]
    if len(finalized_games) > MAX_GAMES_CACHE:
        oldest = sorted(finalized_games.keys())[:len(finalized_games) - MAX_GAMES_CACHE]
        for num in oldest:
            del finalized_games[num]
    if len(predictions) > MAX_PREDICTIONS_STORED:
        active = [p for p in predictions if p.get("status") in ("pending_check", "pending_publish", "pending")]
        inactive = [p for p in predictions if p.get("status") not in ("pending_check", "pending_publish", "pending")]
        keep_count = max(0, MAX_PREDICTIONS_STORED - len(active))
        predictions[:] = inactive[-keep_count:] + active if keep_count else active
        save_predictions()


# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================

def main():
    delete_webhook()
    load_bank()
    load_predictions()
    offset = load_offset()

    print("🚀 БОТ ЗАПУЩЕН", flush=True)

    while True:
        try:
            if should_cleanup_now():
                cleanup_nightly()

            offset = process_telegram_updates(offset, on_game_message)
            finalize_pending_games()
            resolve_pending_publishes()
            check_predictions()
            cleanup_caches()
            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"❌ Ошибка основного цикла: {e}", flush=True)
            time.sleep(3)


# =====================================================================
# ЗАПУСК
# =====================================================================

if __name__ == "__main__":
    import threading
    threading.Thread(target=main, daemon=True).start()
    from web_server import start_web_server
    start_web_server()
    while True:
        time.sleep(60)