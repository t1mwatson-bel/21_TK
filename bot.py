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
DOGON_BETS = (50, 75, 100, 125) # Фиксированные ставки
MAX_DOGON_INDEX = 3

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

def get_latest_game_number():
    all_nums = list(games_cache.keys()) + list(finalized_games.keys()) + list(pending_games.keys())
    if not all_nums:
        return 0
    return max(all_nums)

def calculate_bet_amount(dogon_index):
    index = max(0, min(int(dogon_index), MAX_DOGON_INDEX))
    return DOGON_BETS[index]

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
    active_statuses = ("pending_check", "pending_publish", "pending", "pending_silent", "pending_echo")
    return any(p.get("status") in active_statuses for p in predictions)

# =====================================================================
# СООБЩЕНИЯ TELEGRAM
# =====================================================================

def make_prediction_message(prediction):
    target_games = prediction["target_games"]
    rank = prediction["predicted_rank"]
    suits = prediction["predicted_suits"]
    suits_str = " или ".join([f"{rank}{s}" for s in suits])
    
    bet_total = prediction.get("bet", BASE_BET * 2)
    one_bet = bet_total / 2
    
    games_str = format_games_range(target_games)
    
    # Проверяем, на кого прогноз (игрок или дилер)
    target_who = prediction.get("target_who", "both")
    who_text = ""
    if target_who == "player":
        who_text = " (только у ИГРОКА)"
    elif target_who == "dealer":
        who_text = " (только у ДИЛЕРА)"

    return (
        f"🎯 Игры: <b>{games_str}</b>\n"
        f"Ждем: <b>{suits_str}</b>{who_text}\n"
        f"Ставка: {bet_total}₽ (2×{int(one_bet)}₽)"
    )

def make_result_message(prediction, result, found_cards_data=None):
    target_games = prediction["target_games"]
    rank = prediction["predicted_rank"]
    suits_str = " или ".join([f"{rank}{s}" for s in prediction["predicted_suits"]])
    balance = int(math.ceil(get_balance()))
    games_str = format_games_range(target_games)

    if result == "win":
        if found_cards_data:
            cards_list = [f"{c['card']} у {'игрока' if c['who'] == 'player' else 'дилера'}" for c in found_cards_data]
            cards_text = ", ".join(cards_list)
        else:
            cards_text = ""

        return (
            f"🎯 Игры: <b>{games_str}</b>: {suits_str} ✅\n"
            f"Карты: {cards_text}\n"
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
# СОЗДАНИЕ ТИХОГО ПРОГНОЗА (ЭТАП 1)
# =====================================================================

def create_prediction(game):
    if has_active_prediction():
        return None

    if not game.get("is_finished"):
        return None

    trigger = find_new_trigger(game)
    if not trigger: return None

    trigger_number = game.get("game_number")
    if trigger_number is None: return None
    try:
        trigger_number = int(trigger_number)
    except (ValueError, TypeError): return None

    prediction_data = calculate_prediction(trigger)
    target_games = prediction_data["target_games"]

    prediction = {
        "algorithm": "echo_v1",
        "trigger_number": trigger_number,
        "trigger_game_id": game.get("game_id"),
        "trigger_card": trigger.get("trigger_card_text"),
        "predicted_rank": prediction_data["predicted_rank"],
        "predicted_suits": prediction_data["predicted_suits"],
        "target_games": target_games,
        "publish_game": None,
        "target_who": None,
        
        "base_bet": BASE_BET,
        "bet": 0,
        "dogon": 0,
        
        "status": "pending_silent",
        "created_at": now_moscow().isoformat(),
        "sent_at": None,
        "closed_at": None,
        "message_id": None,
        "pending_publish": False,
        "result_game": None,
        "found_card": None,
        "last_publish_attempt": None,
    }

    predictions.append(prediction)
    save_predictions()
    print(f"🔮 ТИХИЙ #N{trigger_number} -> Цель: {target_games}", flush=True)
    return prediction

# =====================================================================
# ПРОВЕРКА ТИХОГО ПРОГНОЗА (ЭТАП 2)
# =====================================================================

def check_silent_predictions():
    changed = False
    now = now_moscow()

    for p in predictions:
        if p.get("status") != "pending_silent":
            continue

        target_games = p.get("target_games", [])
        
        for game_num in target_games:
            game = games_cache.get(game_num)
            
            if not game or not game.get("is_finished"):
                continue

            # Ищем карту у игрока и дилера
            found_cards = check_prediction_result(game, p)
            
            # Анализируем, у кого выпало
            who_found = set()
            for card_data in found_cards:
                who_found.add(card_data["who"])

            if len(who_found) == 2:
                # Выпало у обоих — пропуск
                p["status"] = "skipped"
                p["closed_at"] = now.isoformat()
                print(f"⏭️ ТИХИЙ #{p['trigger_number']} ПРОПУЩЕН (выпало у обоих в #{game_num})", flush=True)
                changed = True
                break
            elif len(who_found) == 1:
                # Выпало у одного — создаем Эхо
                target_who = list(who_found)[0]
                p["target_who"] = target_who
                
                # Новый пул = первая игра старого пула + 10
                first_game = target_games[0]
                echo_first = first_game + 10
                if echo_first > 1440:
                    echo_first = echo_first - 1440
                
                echo_games = []
                for i in range(4):
                    g_num = echo_first + i
                    if g_num > 1440:
                        g_num = g_num - 1440
                    echo_games.append(g_num)
                
                # Публикация за 2 игры до Д0
                publish_game = echo_games[0] - PUBLISH_OFFSET_GAMES
                if publish_game < 1:
                    publish_game = 1440 + publish_game

                p["target_games"] = echo_games
                p["publish_game"] = publish_game
                p["status"] = "pending_publish"
                p["bet"] = BASE_BET * 2 # 2 ставки (на 2 масти)
                
                print(f"🔄 СОЗДАНО ЭХО #{p['trigger_number']} на {target_who} -> Цель: {echo_games} | Публикация на #{publish_game}", flush=True)
                changed = True
                break

        # Если ни одна игра не подошла (все 4 прошли, а карты не было)
        if p.get("status") == "pending_silent":
            last_game_num = target_games[-1]
            last_game = games_cache.get(last_game_num)
            if last_game and last_game.get("is_finished"):
                p["status"] = "skipped"
                p["closed_at"] = now.isoformat()
                print(f"⏭️ ТИХИЙ #{p['trigger_number']} ПРОПУЩЕН (карта не выпала)", flush=True)
                changed = True

    if changed:
        save_predictions()

# =====================================================================
# ПУБЛИКАЦИЯ ЭХО
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
        
        game = games_cache.get(game_num)
        if not game and game_num in pending_games:
            game = parse_game_message(pending_games[game_num].get("text", ""))

        if not game or not game.get("is_finished"):
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
            continue

        p["result_game"] = game_num
        found_cards_data = check_prediction_result(game, p)

        if found_cards_data:
            p["status"] = "win"
            p["found_card"] = found_cards_data[0]["card"]
            p["closed_at"] = now.isoformat()
            
            apply_win(p, current_dogon, found_cards_data)
            
            if p.get("message_id"):
                telegram_edit(p["message_id"], make_result_message(p, "win", found_cards_data))
            
            cards_log = ", ".join([f"{c['card']} ({'игрок' if c['who']=='player' else 'дилер'})" for c in found_cards_data])
            print(f"✅ ПРОГНОЗ #{p['trigger_number']} ЗАШЕЛ в игре #{game_num}! Карты: {cards_log}", flush=True)
            changed = True
            continue

        if current_dogon < MAX_DOGON_INDEX:
            next_dogon = current_dogon + 1
            apply_lose(p, current_dogon)
            
            apply_dogon_bet(p, next_dogon)
            
            p["dogon"] = next_dogon
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

        if latest_game >= publish_game:
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
        if not info: continue
        game = parse_game_message(info.get("text", ""))
        if game:
            finalized_games[num] = game

# =====================================================================
# ПОЛУЧЕНИЕ ИГРОВЫХ СООБЩЕНИЙ
# =====================================================================

def on_game_message(game_number, text, is_edited):
    if is_waiting_message(text): return

    game = parse_game_message(text)
    if not game or not game.get("player_cards"): return

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
        for num in oldest: del games_cache[num]
    if len(finalized_games) > MAX_GAMES_CACHE:
        oldest = sorted(finalized_games.keys())[:len(finalized_games) - MAX_GAMES_CACHE]
        for num in oldest: del finalized_games[num]
    if len(predictions) > MAX_PREDICTIONS_STORED:
        active = [p for p in predictions if p.get("status") in ("pending_check", "pending_publish", "pending", "pending_silent", "pending_echo")]
        inactive = [p for p in predictions if p.get("status") not in ("pending_check", "pending_publish", "pending", "pending_silent", "pending_echo")]
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
            if should_cleanup_now(): cleanup_nightly()
            offset = process_telegram_updates(offset, on_game_message)
            finalize_pending_games()
            
            # Проверяем тихие прогнозы и создаем эхо
            check_silent_predictions()
            
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