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
PREDICTION_TIMEOUT_MINUTES = 300
BASE_BET = 50.0
DOGON_BETS = (50, 75, 113, 170)
MAX_DOGON_INDEX = 3

# За сколько игр до начала пула публикуем прогноз
PUBLISH_OFFSET_GAMES = 2

# =====================================================================
# ПРОВЕРКА ENV
# =====================================================================

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True); sys.exit(1)
if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True); sys.exit(1)
if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан", flush=True); sys.exit(1)

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
    return games_cache.get(game_number) or finalized_games.get(game_number)

def normalize_message_id(response):
    if isinstance(response, bool): return None
    if isinstance(response, int): return response if response > 0 else None
    if not isinstance(response, dict): return None
    if response.get("message_id"): return response["message_id"]
    result = response.get("result")
    if isinstance(result, dict):
        message_id = result.get("message_id")
        if message_id: return message_id
    return None

def save_state():
    save_predictions()
    save_bank()

def format_games_range(target_games):
    if not target_games: return ""
    if len(target_games) == 1: return f"#{target_games[0]}"
    is_sequential = all(target_games[i] + 1 == target_games[i+1] for i in range(len(target_games)-1))
    if is_sequential:
        return f"{target_games[0]}-{target_games[-1]}"
    return ", ".join(f"#{g}" for g in target_games)

def calculate_bet_amount(dogon_index):
    index = max(0, min(int(dogon_index), MAX_DOGON_INDEX))
    return DOGON_BETS[index]

def get_latest_game_number():
    """Возвращает номер самой свежей игры из кэша."""
    all_nums = list(games_cache.keys()) + list(finalized_games.keys()) + list(pending_games.keys())
    if not all_nums:
        return 0
    return max(all_nums)

# =====================================================================
# ОЧИСТКА
# =====================================================================

def should_cleanup_now(now=None):
    global last_cleanup_date
    if now is None: now = now_moscow()
    if last_cleanup_date == now.date(): return False
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
            predictions = []; return
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
            f.flush(); os.fsync(f.fileno())
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
    suits = prediction["predicted_suits"]
    suits_str = " или ".join([f"{rank}{s}" for s in suits])
    bet = prediction.get("bet", BASE_BET)
    games_str = format_games_range(target_games)
    return (
        f"🎯 Игры: <b>{games_str}</b>\n"
        f"Ждем: <b>{suits_str}</b> | Ставка: {bet}₽"
    )

def make_result_message(prediction, result, found_card=None):
    target_games = prediction["target_games"]
    rank = prediction["predicted_rank"]
    suits_str = " или ".join([f"{rank}{s}" for s in prediction["predicted_suits"]])
    bet = prediction.get("bet", BASE_BET)
    balance = int(math.ceil(get_balance()))
    games_str = format_games_range(target_games)

    if result == "win":
        return (
            f"🎯 Игры: <b>{games_str}</b>: {suits_str} ✅ (карта: {found_card})\n"
            f"🤑 Баланс: {balance}₽"
        )
    if result == "lose":
        return (
            f"🎯 Игры: <b>{games_str}</b>: {suits_str} ❌ (не зашла)\n"
            f"🤑 Баланс: {balance}₽"
        )
    return f"🎯 Игры: <b>{games_str}</b>: {suits_str} ⚠️"

def is_waiting_message(text):
    if not text: return True
    if "Ожидание" in text or "⏳" in text: return True
    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2: return True
    card_pattern = re.compile(r"(?:10|[2-9AJQK])[♠♣♦♥]")
    if not card_pattern.search(groups[0]): return True
    return False

# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(game):
    if has_active_prediction():
        return None

    trigger = find_new_trigger(game)
    if not trigger: return None

    trigger_number = game.get("game_number")
    if trigger_number is None: return None
    try:
        trigger_number = int(trigger_number)
    except (ValueError, TypeError): return None

    before_number = trigger_number - 1
    if before_number < 1: before_number = 1440 
    before_game = get_game(before_number)
    
    if before_game and before_game.get("is_r"):
        print(f"⛔ #N{trigger_number}: #R перед триггером, пропуск", flush=True)
        return None

    prediction_data = calculate_prediction(trigger)
    target_games = prediction_data["target_games"]

    # Вычисляем игру, на которой нужно опубликовать (за N игр до Д0)
    first_target = target_games[0]
    publish_game = first_target - PUBLISH_OFFSET_GAMES
    if publish_game < 1:
        publish_game = 1440 + publish_game  # Цикличность

    prediction = {
        "algorithm": "new_logic_v1",
        "trigger_number": trigger_number,
        "trigger_game_id": game.get("game_id"),
        "trigger_card": trigger.get("trigger_card_text"),
        "predicted_rank": prediction_data["predicted_rank"],
        "predicted_suits": prediction_data["predicted_suits"],
        "target_games": target_games,
        "publish_game": publish_game,
        "base_bet": BASE_BET,
        "bet": DOGON_BETS[0],
        "dogon": 0,
        "status": "pending_publish",  # Ждет публикации
        "created_at": now_moscow().isoformat(),
        "sent_at": None,
        "closed_at": None,
        "message_id": None,
        "pending_publish": True,
        "result_game": None,
        "found_card": None,
        "last_publish_attempt": None,
    }

    predictions.append(prediction)
    save_predictions()
    print(f"🔮 СОЗДАН #N{trigger_number} -> Цель: {target_games} | Публикация на #{publish_game}", flush=True)
    return prediction

# =====================================================================
# ПУБЛИКАЦИЯ ПРОГНОЗА
# =====================================================================

def send_prediction(prediction):
    if prediction.get("message_id"): return True

    message = make_prediction_message(prediction)
    print(f"DEBUG: Пытаюсь отправить сообщение: {message}", flush=True)

    prediction["status"] = "pending_publish"
    prediction["pending_publish"] = True
    prediction["last_publish_attempt"] = now_moscow().isoformat()
    save_predictions()

    try:
        response = telegram_send(message)
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
# ПРОВЕРКА ПРОГНОЗОВ (ГЛАВНАЯ ФУНКЦИЯ)
# =====================================================================

def check_predictions():
    changed = False
    now = now_moscow()

    for p in predictions:
        if p.get("status") != "pending":
            continue

        target_games = p.get("target_games", [])
        current_dogon = int(p.get("dogon", 0))
        
        if current_dogon >= len(target_games):
            continue
            
        game_num = target_games[current_dogon]
        
        # ВСЕГДА берем из games_cache (там самые свежие данные)
        game = games_cache.get(game_num)
        
        # Если нет в кэше, пробуем pending_games
        if not game and game_num in pending_games:
            game = parse_game_message(pending_games[game_num].get("text", ""))

        print(f"DEBUG CHECK: Прогноз #{p['trigger_number']} | Ждем #{game_num} | Догон Д{current_dogon} | Игра: {'НАЙДЕНА' if game else 'НЕТ'}", flush=True)

        if not game:
            sent_at = parse_datetime(p.get("sent_at"), now)
            if sent_at and now - sent_at > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES):
                 p["status"] = "return"
                 p["closed_at"] = now.isoformat()
                 apply_return(p, p.get("bet", 0))
                 if p.get("message_id"):
                    telegram_edit(p["message_id"], make_result_message(p, "return"))
                 print(f"♻️ Возврат #{p['trigger_number']}: таймаут", flush=True)
                 changed = True
            continue

        if isinstance(game, str):
            game = parse_game_message(game)

        if not isinstance(game, dict):
            print(f"⚠️ Игра #{game_num} не словарь: {type(game)}", flush=True)
            continue

        p["result_game"] = game_num
        found_card = check_prediction_result(game, p)

        print(f"DEBUG CHECK: Игра #{game_num} | P: {game.get('player_cards')} | D: {game.get('dealer_cards')} | Карта: {found_card}", flush=True)

        if found_card:
            p["status"] = "win"
            p["found_card"] = found_card
            p["closed_at"] = now.isoformat()
            apply_win(p, current_dogon, found_card)
            if p.get("message_id"):
                telegram_edit(p["message_id"], make_result_message(p, "win", found_card))
            print(f"✅ ПРОГНОЗ #{p['trigger_number']} ЗАШЕЛ в игре #{game_num}! Карта: {found_card}", flush=True)
            changed = True
            continue

        if current_dogon < MAX_DOGON_INDEX:
            next_dogon = current_dogon + 1
            apply_lose(p, current_dogon)
            p["dogon"] = next_dogon
            p["bet"] = calculate_bet_amount(next_dogon)
            p["sent_at"] = now.isoformat()
            if p.get("message_id"):
                telegram_edit(p["message_id"], make_prediction_message(p))
            print(f"❌ D{current_dogon} проиграл, переходим на D{next_dogon} (игра #{target_games[next_dogon]})", flush=True)
            changed = True
            continue

        apply_lose(p, current_dogon)
        p["status"] = "lose"
        p["closed_at"] = now.isoformat()
        if p.get("message_id"):
            telegram_edit(p["message_id"], make_result_message(p, "lose"))
        print(f"❌ ПРОГНОЗ #{p['trigger_number']} ПРОИГРАН (все догоны D0-D3)", flush=True)
        changed = True

    if changed:
        save_state()

# =====================================================================
# ОЖИДАЮЩИЕ ПУБЛИКАЦИИ (С ОТЛОЖЕННОЙ ОТПРАВКОЙ)
# =====================================================================

def resolve_pending_publishes():
    latest_game = get_latest_game_number()

    for p in predictions:
        if p.get("status") != "pending_publish" or p.get("message_id"):
            continue

        publish_game = p.get("publish_game")
        if publish_game is None:
            continue

        # Публикуем, когда текущая игра дошла до publish_game
        if latest_game >= publish_game:
            send_prediction(p)