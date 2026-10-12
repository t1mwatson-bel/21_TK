import os
import sys
import re
import json
import time

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
    add_game_offset,
    find_trigger_v2,
    find_rank_in_game,
)

from coefs import get_player_cf, get_banker_cf

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
    bet_for_dogon,
    apply_dogon_bet,
    apply_win,
    apply_lose,
    apply_return,
)


# =====================================================================
# КОНСТАНТЫ
# =====================================================================

TARGET_OFFSET = 3
PREDICTION_TIMEOUT_MINUTES = 20
FINALIZE_WAIT_SECONDS = 30


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

    if now.time() >= cleanup_time:
        return True

    return False


def cleanup_nightly():
    global games_cache, finalized_games, pending_games, predictions, last_cleanup_date

    now = datetime.now(MOSCOW_TZ)

    print("", flush=True)
    print("🧹 НОЧНАЯ ОЧИСТКА (03:00)", flush=True)

    games_count = len(games_cache)
    games_cache.clear()

    final_count = len(finalized_games)
    finalized_games.clear()

    pending_count = len(pending_games)
    pending_games.clear()

    print(
        f"   🗑️ games_cache: {games_count}, "
        f"finalized: {final_count}, "
        f"pending: {pending_count}",
        flush=True,
    )

    last_cleanup_date = now.date()

    print("✅ Ночная очистка завершена", flush=True)
    print("", flush=True)


# =====================================================================
# ПРОГНОЗЫ — LOAD / SAVE
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
        print(f"⚠️ Ошибка чтения {PREDICTIONS_FILE}: {e}", flush=True)
        predictions = []


def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


# =====================================================================
# АКТИВНЫЙ ПРОГНОЗ
# =====================================================================

def has_active_prediction():
    for prediction in predictions:
        if prediction.get("status") == "pending":
            return True
    return False


# =====================================================================
# ФОРМАТ СООБЩЕНИЙ
# =====================================================================

def make_prediction_message(prediction):
    target = prediction["target_number"]
    rank = prediction["predicted_rank"]
    return f"🎯 Игра: <b>#N{target}</b>: {rank}"


def make_result_message(prediction, result, found_card=None):

    target = prediction["target_number"]
    rank = prediction["predicted_rank"]

    if result == "win":
        where = found_card.get("where") if found_card else None
        if where == "dealer":
            where_text = " (банкир)"
        elif where == "player":
            where_text = " (игрок)"
        else:
            where_text = ""
        return f"🎯 Игра: <b>#N{target}</b>: {rank} ✅{where_text}"

    elif result == "lose":
        return f"🎯 Игра: <b>#N{target}</b>: {rank} ❌"

    elif result == "return":
        return f"🎯 Игра: <b>#N{target}</b>: {rank} ♻️"

    return f"🎯 Игра: <b>#N{target}</b>: {rank} ⚠️"


# =====================================================================
# ПРОВЕРКА "ОЖИДАНИЕ"
# =====================================================================

def is_waiting_message(text):
    if not text:
        return True

    if "Ожидание" in text:
        return True

    if "⏳" in text:
        return True

    groups = re.findall(r"\(([^()]*)\)", text)

    if len(groups) < 2:
        return True

    card_pattern = re.compile(r"[2-9AJQK10][♠♣♦♥]")

    if not card_pattern.search(groups[0]):
        return True

    if not card_pattern.search(groups[1]):
        return True

    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(game):

    if has_active_prediction():
        return None

    trigger = find_trigger_v2(game)

    if not trigger:
        return None

    trigger_number = game["game_number"]
    trigger_id = game.get("game_id")

    predicted_rank = trigger["predicted_rank"]

    target_number = add_game_offset(trigger_number, TARGET_OFFSET)

    # Проверка дубля
    for old in predictions:
        if old.get("status") != "pending":
            continue
        if (
            old.get("target_number") == target_number
            and old.get("predicted_rank") == predicted_rank
        ):
            return None

    base_bet = get_current_bet()

    cf_player = get_player_cf(predicted_rank)
    cf_banker = get_banker_cf(predicted_rank)

    prediction = {
        "algorithm": "baccarat_rank_v1",

        "trigger_number": trigger_number,
        "trigger_game_id": trigger_id,
        "trigger_card": trigger["trigger_card"],
        "third_card": trigger["third_card"],

        "predicted_rank": predicted_rank,

        "cf_player": cf_player,
        "cf_banker": cf_banker,

        "target_offset": TARGET_OFFSET,
        "target_number": target_number,

        "base_bet": base_bet,
        "bet": base_bet,

        "status": "pending",
        "dogon": 0,

        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "sent_at": None,
        "closed_at": None,

        "message_id": None,

        "result_game": None,
        "found_card": None,
        "close_reason": None,
    }

    predictions.append(prediction)
    save_predictions()

    print("", flush=True)
    print("🔮 ПРОГНОЗ СОЗДАН", flush=True)
    print(f"📌 Триггер: #N{trigger_number}", flush=True)
    print(
        f"🃏 Карты: {trigger['trigger_card']} ... {trigger['third_card']}",
        flush=True,
    )
    print(f"🎯 Прогноз: ранг {predicted_rank}", flush=True)
    print(f"🎯 Цель: #N{target_number}", flush=True)
    print(f"💰 Ставка Д0: {base_bet} ₽", flush=True)

    send_prediction(prediction)

    return prediction


# =====================================================================
# ОТПРАВКА ПРОГНОЗА
# =====================================================================

def send_prediction(prediction):

    message = make_prediction_message(prediction)
    message_id = telegram_send(message)

    if not message_id:
        print(
            f"❌ Не удалось отправить #N{prediction['target_number']}",
            flush=True,
        )
        return False

    prediction["message_id"] = message_id
    prediction["sent_at"] = datetime.now(MOSCOW_TZ).isoformat()

    apply_dogon_bet(prediction, 0)

    save_predictions()

    print(
        f"📤 ПРОГНОЗ ОТПРАВЛЕН: "
        f"#N{prediction['target_number']} "
        f"ранг {prediction['predicted_rank']}",
        flush=True,
    )

    return True


# =====================================================================
# ФИНАЛИЗАЦИЯ ИГР
# =====================================================================

def finalize_pending_games():

    now = time.time()
    ready = []

    for (game_number, info) in list(pending_games.items()):
        last_update = info.get("last_update", info.get("first_seen", now))
        if now - last_update >= FINALIZE_WAIT_SECONDS:
            ready.append(game_number)

    for game_number in ready:
        info = pending_games.pop(game_number, None)
        if not info:
            continue
        text = info.get("text", "")
        game = parse_game_message(text)
        if not game:
            continue
        finalized_games[game_number] = game
        print(f"✅ #N{game_number} → finalized", flush=True)


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def check_predictions():

    changed = False
    now = datetime.now(MOSCOW_TZ)

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get("target_number")

        if not target:
            continue

        # Таймаут
        sent_at_str = prediction.get("sent_at")

        if sent_at_str:
            try:
                sent_at = datetime.fromisoformat(sent_at_str)
                if now - sent_at > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES):
                    prediction["status"] = "return"
                    prediction["close_reason"] = "timeout"
                    prediction["closed_at"] = now.isoformat()

                    telegram_edit(
                        prediction.get("message_id"),
                        make_result_message(prediction, "return"),
                    )

                    apply_return(prediction, prediction.get("bet", 0))

                    print(
                        f"♻️ ВОЗВРАТ #N{target} "
                        f"(timeout > {PREDICTION_TIMEOUT_MINUTES} мин)",
                        flush=True,
                    )

                    changed = True
                    continue
            except Exception:
                pass

        # Поиск ранга
        current_dogon = prediction.get("dogon", 0)
        won = False
        found_card = None
        win_dogon = None

        for dogon_index in range(0, DOGON_GAMES + 1):

            game_number = add_game_offset(target, dogon_index)
            game = finalized_games.get(game_number)

            if not game:
                continue

            found = find_rank_in_game(game, prediction["predicted_rank"])

            if found:
                won = True
                found_card = found
                win_dogon = dogon_index
                break

        if won:

            prediction["status"] = "win"
            prediction["result_game"] = add_game_offset(target, win_dogon)
            prediction["found_card"] = found_card
            prediction["dogon"] = win_dogon
            prediction["closed_at"] = now.isoformat()

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "win", found_card),
            )

            apply_win(prediction, win_dogon, found_card)

            print("", flush=True)
            print(f"✅ ПРОГНОЗ ЗАШЁЛ #N{target}", flush=True)
            print(f"🎯 Ранг: {prediction['predicted_rank']}", flush=True)
            print(
                f"🃏 Найдена: {found_card['card']} ({found_card['where']})",
                flush=True,
            )
            print(f"🔄 Догон: Д{win_dogon}", flush=True)

            changed = True
            continue

        # Проверяем, все ли игры финализированы
        all_finalized = True

        for dogon_index in range(0, DOGON_GAMES + 1):
            game_number = add_game_offset(target, dogon_index)
            if game_number not in finalized_games:
                all_finalized = False
                break

        if not all_finalized:
            continue

        # LOSE
        if current_dogon < DOGON_GAMES:

            next_dogon = current_dogon + 1
            apply_lose(prediction, current_dogon)
            prediction["dogon"] = next_dogon

            print(
                f"❌ Д{current_dogon} проиграл, "
                f"переходим на Д{next_dogon}",
                flush=True,
            )

            changed = True
            continue

        apply_lose(prediction, current_dogon)

        prediction["status"] = "lose"
        prediction["closed_at"] = now.isoformat()

        telegram_edit(
            prediction.get("message_id"),
            make_result_message(prediction, "lose"),
        )

        print("", flush=True)
        print(f"❌ ПРОГНОЗ НЕ ЗАШЁЛ #N{target}", flush=True)

        changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА ИГР TELEGRAM
# =====================================================================

def on_game_message(game_number, text, is_edited):

    if is_waiting_message(text):
        return

    game = parse_game_message(text)

    if not game:
        return

    if not game.get("player_cards") or not game.get("dealer_cards"):
        return

    is_new = game_number not in games_cache
    games_cache[game_number] = game

    pending_games[game_number] = {
        "text": text,
        "last_update": time.time(),
        "first_seen": time.time(),
    }

    log_game(game)

    create_prediction(game)


# =====================================================================
# ОЧИСТКА КЭШЕЙ
# =====================================================================

def cleanup_games_cache():
    if len(games_cache) <= MAX_GAMES_CACHE:
        return
    items = sorted(
        games_cache.items(),
        key=lambda kv: kv[1].get("received_at", ""),
    )
    for number, _ in items[:-MAX_GAMES_CACHE]:
        del games_cache[number]


def cleanup_finalized_games():
    if len(finalized_games) <= MAX_GAMES_CACHE:
        return
    items = sorted(
        finalized_games.items(),
        key=lambda kv: kv[1].get("received_at", ""),
    )
    for number, _ in items[:-MAX_GAMES_CACHE]:
        del finalized_games[number]


def cleanup_predictions():
    global predictions
    if len(predictions) > MAX_PREDICTIONS_STORED:
        predictions = predictions[-MAX_PREDICTIONS_STORED:]
        save_predictions()


# =====================================================================
# ГЛАВНЫЙ ЦИКЛ
# =====================================================================

def main():

    global predictions

    print("", flush=True)
    print("==================================================", flush=True)
    print("🚀 BACCARAT — RANK PREDICTOR v1", flush=True)
    print("==================================================", flush=True)
    print("📡 Игры: CHANNEL_STATS", flush=True)
    print("📤 Прогнозы: CHANNEL_PROGNOZ", flush=True)
    print("🎯 Алгоритм: 1-я < 3-я (2-10)", flush=True)
    print(f"🎯 Сдвиг цели: +{TARGET_OFFSET}", flush=True)
    print("💸 Ставок на прогноз: 2 (игрок + банкир)", flush=True)
    print(f"🔄 Догоны: Д0..Д{DOGON_GAMES}", flush=True)
    print(f"⏰ Таймаут: {PREDICTION_TIMEOUT_MINUTES} мин", flush=True)
    print(f"⏳ Финализация: {FINALIZE_WAIT_SECONDS} сек", flush=True)
    print("==================================================", flush=True)

    delete_webhook()
    load_bank()
    load_predictions()

    offset = load_offset()

    print(f"📌 Telegram offset: {offset}", flush=True)
    print(f"📊 Загружено прогнозов: {len(predictions)}", flush=True)
    print("==================================================", flush=True)
    print("🟢 БОТ ГОТОВ", flush=True)
    print("==================================================", flush=True)

    while True:
        try:
            if should_cleanup_now():
                cleanup_nightly()

            offset = process_telegram_updates(offset, on_game_message)

            finalize_pending_games()
            check_predictions()
            cleanup_games_cache()
            cleanup_finalized_games()
            cleanup_predictions()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен", flush=True)
            break

        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(3)


# =====================================================================
# СТАРТ
# =====================================================================

if __name__ == "__main__":
    import threading
    import time

    threading.Thread(target=main, daemon=True).start()

    from web_server import start_web_server
    start_web_server()

    while True:
        time.sleep(60)