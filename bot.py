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
    TARGET_OFFSET,
    PUBLISH_OFFSET,
)

from parsers import (
    parse_game_message,
    log_game,
    add_game_offset,
    find_trigger_21,
    find_rank_in_player,
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

PREDICTION_TIMEOUT_MINUTES = 300  # 5 часов
FINALIZE_WAIT_SECONDS = 30
PENDING_CHECK_TIMEOUT_MINUTES = 5
DOGON_MULTIPLIER = 1.5

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
# ОЧИСТКА
# =====================================================================

def should_cleanup_now(now=None):
    global last_cleanup_date
    if now is None:
        now = datetime.now(MOSCOW_TZ)
    if last_cleanup_date == now.date():
        return False
    cleanup_time = dtime(CLEANUP_HOUR, CLEANUP_MINUTE)
    return now.time() >= cleanup_time

def cleanup_nightly():
    global games_cache, finalized_games, pending_games, last_cleanup_date
    now = datetime.now(MOSCOW_TZ)
    print("🧹 НОЧНАЯ ОЧИСТКА", flush=True)
    games_cache.clear()
    finalized_games.clear()
    pending_games.clear()
    last_cleanup_date = now.date()

# =====================================================================
# ПРОГНОЗЫ
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
    except Exception:
        predictions = []

def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PREDICTIONS_FILE)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}", flush=True)

def has_active_prediction():
    for p in predictions:
        if p.get("status") in ("pending", "pending_check", "pending_publish"):
            return True
    return False

def find_r_anomaly(game_number):
    game = finalized_games.get(game_number) or games_cache.get(game_number)
    if not game:
        return None
    return bool(game.get("is_r"))

# =====================================================================
# СТАВКИ И СООБЩЕНИЯ
# =====================================================================

def calculate_bet_amount(base_bet, dogon_index):
    amount = base_bet * (DOGON_MULTIPLIER ** dogon_index)
    return int(math.ceil(amount))

def make_prediction_message(prediction, dogon_index):
    target = prediction["target_number"]
    rank = prediction["predicted_rank"]
    base_bet = prediction.get("base_bet", 50)
    bet_amount = calculate_bet_amount(base_bet, dogon_index)
    return f"🎯 Игра: <b>#N{target}</b>: {rank} ставка {bet_amount}₽"

def make_result_message(prediction, result, dogon_index, found_card=None):
    target = prediction["target_number"]
    rank = prediction["predicted_rank"]
    base_bet = prediction.get("base_bet", 50)
    bet_amount = calculate_bet_amount(base_bet, dogon_index)
    balance = int(math.ceil(get_balance()))

    if result == "win":
        return f"🎯 Игра: <b>#N{target}</b>: {rank} ✅ при {bet_amount}₽\n🤑 Баланс: {balance}₽"
    elif result == "lose":
        return f"🎯 Игра: <b>#N{target}</b>: {rank} ❌\n🤑 Баланс: {balance}₽"
    elif result == "return":
        return f"🎯 Игра: <b>#N{target}</b>: {rank} ♻️\n🤑 Баланс: {balance}₽"
    return f"🎯 Игра: <b>#N{target}</b>: {rank} ⚠️"

def is_waiting_message(text):
    if not text:
        return True
    if "Ожидание" in text or "⏳" in text:
        return True
    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2:
        return True
    # Проверяем, есть ли хотя бы одна карта у игрока
    card_pattern = re.compile(r"[2-9AJQK10][♠♣♦♥]")
    if not card_pattern.search(groups[0]):
        return True
    # Дилер может быть пустым () — это норма для 21
    return False

# =====================================================================
# ЛОГИКА ПРОГНОЗА
# =====================================================================

def create_prediction(game):
    if has_active_prediction():
        return None
        
    trigger = find_trigger_21(game)
    if not trigger:
        return None

    trigger_number = game["game_number"]
    predicted_rank = trigger["predicted_rank"]

    # Жесткая проверка «перед»
    if find_r_anomaly(trigger_number - 1) is True:
        print(f"⛔ #N{trigger_number}: #R перед триггером, пропуск", flush=True)
        return None

    target_number = add_game_offset(trigger_number, TARGET_OFFSET)
    publish_number = add_game_offset(target_number, -PUBLISH_OFFSET)

    for old in predictions:
        if old.get("status") in ("pending", "pending_check", "pending_publish"):
            if old.get("target_number") == target_number and old.get("predicted_rank") == predicted_rank:
                return None

    base_bet = get_current_bet()
    prediction = {
        "algorithm": "cyber21_rank_v4",
        "trigger_number": trigger_number,
        "trigger_game_id": game.get("game_id"),
        "trigger_card": trigger["trigger_card"],
        "predicted_rank": predicted_rank,
        "cf_player": get_player_cf(predicted_rank),
        "target_offset": TARGET_OFFSET,
        "target_number": target_number,
        "publish_number": publish_number,
        "base_bet": base_bet,
        "bet": base_bet,
        "status": "pending_check",
        "dogon": 0,
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "sent_at": None,
        "closed_at": None,
        "message_id": None,
        "result_game": None,
        "found_card": None,
        "close_reason": None,
        "check_game_number": trigger_number + 1,
    }

    predictions.append(prediction)
    save_predictions()
    print(f"🔮 СОЗДАН #N{trigger_number} -> Цель #{target_number} (Публикация #{publish_number})", flush=True)
    return prediction

def send_prediction(prediction):
    dogon_index = prediction.get("dogon", 0)
    message = make_prediction_message(prediction, dogon_index)
    message_id = telegram_send(message)
    if not message_id:
        return False

    prediction["message_id"] = message_id
    prediction["sent_at"] = datetime.now(MOSCOW_TZ).isoformat()
    prediction["status"] = "pending"
    apply_dogon_bet(prediction, dogon_index)
    save_predictions()
    print(f"📤 ОТПРАВЛЕН #{prediction['target_number']} (ставка {calculate_bet_amount(prediction['base_bet'], dogon_index)}₽)", flush=True)
    return True

def resolve_pending_checks():
    now = datetime.now(MOSCOW_TZ)
    changed = False
    for p in predictions:
        if p.get("status") != "pending_check":
            continue
            
        check_num = p.get("check_game_number")
        check_game = games_cache.get(check_num) or finalized_games.get(check_num)

        if check_game:
            if check_game.get("is_r"):
                p["status"] = "cancelled"
                p["close_reason"] = "anomaly_after"
                p["closed_at"] = now.isoformat()
                print(f"⛔ ОТМЕНА #{p['trigger_number']}: #R после триггера", flush=True)
            else:
                p["status"] = "pending_publish"
                print(f"✅ #{p['trigger_number']} чист, ждем публикации #{p['publish_number']}", flush=True)
            changed = True
        else:
            # Проверка на пропуск игры
            skipped = any(num > check_num for num in list(games_cache.keys()) + list(finalized_games.keys()))
            if skipped:
                p["status"] = "cancelled"
                p["close_reason"] = "skipped"
                p["closed_at"] = now.isoformat()
                print(f"⛔ ОТМЕНА #{p['trigger_number']}: игра #{check_num} пропущена", flush=True)
                changed = True
            else:
                created = datetime.fromisoformat(p["created_at"])
                if now - created > timedelta(minutes=PENDING_CHECK_TIMEOUT_MINUTES):
                    p["status"] = "cancelled"
                    p["close_reason"] = "check_timeout"
                    p["closed_at"] = now.isoformat()
                    print(f"⛔ ОТМЕНА #{p['trigger_number']}: таймаут проверки", flush=True)
                    changed = True
    if changed:
        save_predictions()

def resolve_pending_publishes():
    now = datetime.now(MOSCOW_TZ)
    changed = False
    all_games = list(games_cache.keys()) + list(finalized_games.keys())
    latest_game = max(all_games) if all_games else 0

    for p in predictions:
        if p.get("status") != "pending_publish":
            continue
            
        pub_num = p.get("publish_number")
        if latest_game >= pub_num:
            if send_prediction(p):
                changed = True
        else:
            created = datetime.fromisoformat(p["created_at"])
            if now - created > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES):
                p["status"] = "cancelled"
                p["close_reason"] = "publish_timeout"
                p["closed_at"] = now.isoformat()
                print(f"⛔ ОТМЕНА #{p['trigger_number']}: таймаут публикации", flush=True)
                changed = True
    if changed:
        save_predictions()

def finalize_pending_games():
    now = time.time()
    ready = [num for num, info in pending_games.items() if now - info.get("last_update", now) >= FINALIZE_WAIT_SECONDS]
    for num in ready:
        info = pending_games.pop(num, None)
        if not info:
            continue
        game = parse_game_message(info.get("text", ""))
        if game:
            finalized_games[num] = game

def check_predictions():
    changed = False
    now = datetime.now(MOSCOW_TZ)

    for p in predictions:
        if p.get("status") != "pending":
            continue
            
        target = p.get("target_number")
        current_dogon = p.get("dogon", 0)
        game_number = add_game_offset(target, current_dogon)
        game = finalized_games.get(game_number)

        if not game:
            # Если игры нет, проверяем таймаут (чтобы не висело вечно)
            sent_at = datetime.fromisoformat(p["sent_at"])
            if now - sent_at > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES):
                p["status"] = "return"
                p["closed_at"] = now.isoformat()
                telegram_edit(p["message_id"], make_result_message(p, "return", current_dogon))
                apply_return(p, p.get("bet", 0))
                changed = True
            continue

        found = find_rank_in_player(game, p["predicted_rank"])
        if found:
            p["status"] = "win"
            p["dogon"] = current_dogon
            p["found_card"] = found
            p["closed_at"] = now.isoformat()
            telegram_edit(p["message_id"], make_result_message(p, "win", current_dogon, found))
            apply_win(p, current_dogon, found)
            print(f"✅ ПРОГНОЗ #{target} ЗАШЕЛ на Д{current_dogon}", flush=True)
            changed = True
        else:
            if current_dogon < DOGON_GAMES:
                next_dogon = current_dogon + 1
                apply_lose(p, current_dogon)
                p["dogon"] = next_dogon
                p["sent_at"] = now.isoformat()
                telegram_edit(p["message_id"], make_prediction_message(p, next_dogon))
                apply_dogon_bet(p, next_dogon)
                print(f"❌ Д{current_dogon} проиграл, Д{next_dogon} (ставка {calculate_bet_amount(p['base_bet'], next_dogon)}₽)", flush=True)
                changed = True
            else:
                apply_lose(p, current_dogon)
                p["status"] = "lose"
                p["closed_at"] = now.isoformat()
                telegram_edit(p["message_id"], make_result_message(p, "lose", current_dogon))
                print(f"❌ ПРОГНОЗ #{target} ПРОИГРАН (все догоны)", flush=True)
                changed = True
                
    if changed:
        save_predictions()

# =====================================================================
# ЦИКЛ И СТАРТ
# =====================================================================

def on_game_message(game_number, text, is_edited):
    if is_waiting_message(text):
        return
    game = parse_game_message(text)
    if not game or not game.get("player_cards"):
        return

    games_cache[game_number] = game
    pending_games[game_number] = {"text": text, "last_update": time.time()}
    log_game(game)
    create_prediction(game)

def cleanup_caches():
    if len(games_cache) > MAX_GAMES_CACHE:
        del games_cache[min(games_cache.keys())]
    if len(finalized_games) > MAX_GAMES_CACHE:
        del finalized_games[min(finalized_games.keys())]
    if len(predictions) > MAX_PREDICTIONS_STORED:
        del predictions[:-MAX_PREDICTIONS_STORED]

def main():
    global predictions
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
            resolve_pending_checks()
            resolve_pending_publishes()
            check_predictions()
            cleanup_caches()
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    import threading
    threading.Thread(target=main, daemon=True).start()
    from web_server import start_web_server
    start_web_server()
    while True:
        time.sleep(60)
