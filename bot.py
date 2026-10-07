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
    FINALIZE_WAIT_SECONDS,
    DOGON_GAMES,
    TOTAL_CHECK_GAMES,
    PREDICTION_TIMEOUT_MINUTES,
    SLEEP_HOUR,
    SLEEP_MINUTE,
    WAKE_HOUR,
    WAKE_MINUTE,
    LAST_PREDICTION_HOUR,
    LAST_PREDICTION_MINUTE,
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
    find_trigger,
    find_card_in_game,     # ← новая функция
    normalize_suit,
    card_to_text,
    cards_to_text,
)

from coefs import get_dealer_cf

from telegram_api import (
    delete_webhook,
    telegram_send,
    telegram_edit,
    process_telegram_updates,
    load_offset,
)

from bank import (
    load_bank,
    save_bank,
    get_current_bet,
    bet_for_dogon,
    apply_win,
    apply_lose,
    apply_return,
)


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
pending_games = {}
predictions = []

last_cleanup_date = None


# =====================================================================
# РАСПИСАНИЕ СНА
# =====================================================================

def is_sleep_time(now=None):

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    current = now.time()

    sleep_start = dtime(SLEEP_HOUR, SLEEP_MINUTE)
    wake_start = dtime(WAKE_HOUR, WAKE_MINUTE)

    if sleep_start <= current or current < wake_start:
        return True

    return False


def is_last_prediction_time(now=None):

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    current = now.time()

    cutoff = dtime(LAST_PREDICTION_HOUR, LAST_PREDICTION_MINUTE)
    sleep_start = dtime(SLEEP_HOUR, SLEEP_MINUTE)

    if cutoff <= current < sleep_start:
        return True

    return False


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


# =====================================================================
# НОЧНАЯ ОЧИСТКА
# =====================================================================

def cleanup_nightly():

    global games_cache
    global pending_games
    global predictions
    global last_cleanup_date

    now = datetime.now(MOSCOW_TZ)

    print("", flush=True)
    print("🧹 НОЧНАЯ ОЧИСТКА (03:00)", flush=True)

    games_count = len(games_cache)
    games_cache.clear()

    print(f"   🗑️ games_cache: удалено {games_count}", flush=True)

    pending_count = len(pending_games)
    pending_games.clear()

    print(f"   🗑️ pending_games: удалено {pending_count}", flush=True)

    last_cleanup_date = now.date()

    print("✅ Ночная очистка завершена", flush=True)
    print("", flush=True)


# =====================================================================
# ПРОГНОЗЫ — LOAD
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


# =====================================================================
# ПРОГНОЗЫ — SAVE
# =====================================================================

def save_predictions():

    try:

        tmp = PREDICTIONS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:

        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


# =====================================================================
# АКТИВНЫЙ ПРОГНОЗ (учитывает и "preparing", и "pending")
# =====================================================================

def has_active_prediction():

    for prediction in predictions:

        if prediction.get("status") in ("pending", "preparing"):
            return True

    return False


# =====================================================================
# ФОРМАТ ПРОГНОЗА
# =====================================================================

def make_prediction_message(prediction):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    return f"🎯 Игра: <b>#N{target}</b>: {card}"


def make_result_message(prediction, result):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    if result == "win":
        mark = " ✅"
    elif result == "lose":
        mark = " ❌"
    elif result == "return":
        mark = " ♻️"
    else:
        mark = " ⚠️"

    return f"🎯 Игра: <b>#N{target}</b>: {card}{mark}"


# =====================================================================
# ПРЕДУПРЕЖДЕНИЕ ЗА 7 ИГР ДО ЦЕЛИ
# =====================================================================

def send_upcoming_warning(prediction):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    message = (
        f"⏳ <b>Скоро прогноз!</b>\n"
        f"🎯 Цель: <b>#N{target}</b>\n"
        f"🃏 Карта: <b>{card}</b>\n"
        f"⏱ Готовимся..."
    )

    message_id = telegram_send(message)

    if message_id:
        print(
            f"⚠️ ПРЕДУПРЕЖДЕНИЕ ОТПРАВЛЕНО: #N{target} {card}",
            flush=True,
        )
    else:
        print(
            f"❌ Не удалось отправить предупреждение #N{target}",
            flush=True,
        )


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА (НЕ отправляет сразу)
# =====================================================================

def create_prediction(game):

    """
    АЛГОРИТМ:

    J/Q/K/A → 10

    прогноз:
        ранг карты перед 10
        +
        масть 10

    сдвиг:
        количество карт игрока × 10

    проверка:
        Д0..Д3

    карта ищется только у дилера.

    ВАЖНО: прогноз НЕ отправляется сразу.
    Сначала предупреждение за 7 игр, потом сам прогноз за 3 игры.
    """

    # ---------------------------------------------------------------
    # Пока есть активный прогноз — новый не создаём
    # ---------------------------------------------------------------

    if has_active_prediction():
        return None

    # ---------------------------------------------------------------
    # Ищем триггер
    # ---------------------------------------------------------------

    trigger = find_trigger(game)

    if not trigger:
        return None

    # ---------------------------------------------------------------
    # Данные триггера
    # ---------------------------------------------------------------

    trigger_number = game["game_number"]
    trigger_id = game.get("game_id")

    predicted_card = trigger["predicted_card"]
    player_card_count = trigger["player_card_count"]
    target_offset = trigger["target_offset"]

    # ---------------------------------------------------------------
    # Цель
    # ---------------------------------------------------------------

    target_number = add_game_offset(trigger_number, target_offset)

    # ---------------------------------------------------------------
    # Проверка дубля
    # ---------------------------------------------------------------

    for old in predictions:

        if old.get("status") not in ("pending", "preparing"):
            continue

        if (
            old.get("target_number") == target_number
            and old.get("predicted_card") == predicted_card
        ):
            return None

    # ---------------------------------------------------------------
    # Ставка и коэффициент
    # ---------------------------------------------------------------

    base_bet = get_current_bet()
    cf = get_dealer_cf(predicted_card)

    # ---------------------------------------------------------------
    # Создаём прогноз
    # ---------------------------------------------------------------

    prediction = {

        "algorithm": "rank_before_10_plus_suit_10",

        "trigger_number": trigger_number,
        "trigger_game_id": trigger_id,
        "trigger_card": trigger["trigger_card"],
        "ten_card": trigger["ten_card"],

        "predicted_rank": trigger["predicted_rank"],
        "predicted_suit": trigger["predicted_suit"],
        "predicted_card": predicted_card,

        "cf": cf,

        "player_card_count": player_card_count,
        "target_offset": target_offset,
        "target_number": target_number,

        "base_bet": base_bet,
        "bet": base_bet,

        "status": "preparing",
        "warning_sent": False,
        "ready_to_send": False,

        "dogon": None,

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

    # ---------------------------------------------------------------
    # ЛОГ
    # ---------------------------------------------------------------

    warning_game = add_game_offset(target_number, -7)
    send_game = add_game_offset(target_number, -3)

    print("", flush=True)
    print("🔮 ПРОГНОЗ ПОДГОТОВЛЕН (ждём подход к цели)", flush=True)
    print(f"📌 Триггер: #N{trigger_number}", flush=True)
    print(
        f"🃏 Триггер: {trigger['trigger_card']} → {trigger['ten_card']}",
        flush=True,
    )
    print(f"🎯 Прогноз: {predicted_card} (cf {cf})", flush=True)
    print(f"👤 Карт игрока: {player_card_count}", flush=True)
    print(f"⏳ Сдвиг: +{target_offset}", flush=True)
    print(f"🎯 Цель: #N{target_number}", flush=True)
    print(f"⏳ Ждём игру #N{warning_game} для предупреждения", flush=True)
    print(f"⏳ Ждём игру #N{send_game} для отправки", flush=True)

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

    save_predictions()

    print(
        f"📤 ПРОГНОЗ ОТПРАВЛЕН: "
        f"#N{prediction['target_number']} "
        f"{prediction['predicted_card']}",
        flush=True,
    )

    return True


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def check_predictions():

    changed = False
    now = datetime.now(MOSCOW_TZ)

    for prediction in predictions:

        status = prediction.get("status")

        # -----------------------------------------------------------
        # ПОДГОТОВКА: ждём игру target-7 и target-3
        # -----------------------------------------------------------

        if status == "preparing":

            target = prediction.get("target_number")

            if not target:
                continue

            # 1. Предупреждение за 7 игр до цели
            if not prediction.get("warning_sent"):

                warning_game = add_game_offset(target, -7)

                if warning_game in games_cache:

                    send_upcoming_warning(prediction)
                    prediction["warning_sent"] = True
                    changed = True

            # 2. Отправка прогноза за 3 игры до цели
            if not prediction.get("ready_to_send"):

                send_game = add_game_offset(target, -3)

                if send_game in games_cache:

                    prediction["status"] = "pending"
                    prediction["ready_to_send"] = True

                    send_prediction(prediction)
                    changed = True

            continue

        # -----------------------------------------------------------
        # ОБЫЧНАЯ ПРОВЕРКА PENDING
        # -----------------------------------------------------------

        if status != "pending":
            continue

        target = prediction.get("target_number")

        if not target:
            continue

        # -----------------------------------------------------------
        # ТАЙМАУТ
        # -----------------------------------------------------------

        sent_at_str = prediction.get("sent_at")

        if sent_at_str:

            try:

                sent_at = datetime.fromisoformat(sent_at_str)

                if (
                    now - sent_at
                    > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES)
                ):

                    prediction["status"] = "return"
                    prediction["close_reason"] = "timeout"
                    prediction["closed_at"] = now.isoformat()

                    telegram_edit(
                        prediction.get("message_id"),
                        make_result_message(prediction, "return"),
                    )

                    apply_return(
                        prediction,
                        prediction.get("bet", 0),
                    )

                    print(
                        f"♻️ ВОЗВРАТ #N{target} "
                        f"(timeout > {PREDICTION_TIMEOUT_MINUTES} мин)",
                        flush=True,
                    )

                    changed = True
                    continue

            except Exception:
                pass

        # -----------------------------------------------------------
        # Д0..Д3
        # -----------------------------------------------------------

        checked_games = []
        waiting = False
        won = False

        base_bet = prediction.get(
            "base_bet",
            prediction.get("bet", 0),
        )

        for dogon in range(0, DOGON_GAMES + 1):

            game_number = add_game_offset(target, dogon)
            game = games_cache.get(game_number)

            # -------------------------------------------------------
            # Игры ещё нет
            # -------------------------------------------------------

            if not game:
                waiting = True
                break

            checked_games.append(game_number)

            # -------------------------------------------------------
            # ИЩЕМ КОНКРЕТНУЮ КАРТУ У ДИЛЕРА
            # -------------------------------------------------------

            found_card = find_card_in_game(game, prediction["predicted_card"])

            if found_card:

                bet_amount = bet_for_dogon(base_bet, dogon)
                cf = get_dealer_cf(prediction["predicted_card"])

                prediction["status"] = "win"
                prediction["result_game"] = game_number
                prediction["found_card"] = found_card
                prediction["dogon"] = dogon
                prediction["bet"] = bet_amount
                prediction["cf"] = cf
                prediction["payout"] = round(bet_amount * cf, 2)
                prediction["closed_at"] = now.isoformat()

                telegram_edit(
                    prediction.get("message_id"),
                    make_result_message(prediction, "win"),
                )

                apply_win(prediction, dogon, bet_amount, cf=cf)

                print("", flush=True)
                print(f"✅ ПРОГНОЗ ЗАШЁЛ #N{target}", flush=True)
                print(
                    f"🎯 Карта: {prediction['predicted_card']}",
                    flush=True,
                )
                print(f"🃏 Найдена: {found_card}", flush=True)
                print(f"🔄 Догон: Д{dogon}", flush=True)
                print(f"💰 Ставка: {bet_amount} ₽", flush=True)
                print(
                    f"🎰 cf: {cf} → "
                    f"выплата {round(bet_amount * cf, 2)} ₽",
                    flush=True,
                )
                print(f"🎰 Игра: #N{game_number} (ДИЛЕР)", flush=True)

                changed = True
                won = True
                break

        # -----------------------------------------------------------
        # ПОБЕДА
        # -----------------------------------------------------------

        if won:
            continue

        # -----------------------------------------------------------
        # Ещё не все игры появились
        # -----------------------------------------------------------

        if waiting:
            continue

        # -----------------------------------------------------------
        # ВСЕ 4 ИГРЫ ПРОВЕРЕНЫ
        # -----------------------------------------------------------

        bet_amount = base_bet

        prediction["status"] = "lose"
        prediction["result_game"] = (
            checked_games[-1]
            if checked_games
            else add_game_offset(target, DOGON_GAMES)
        )
        prediction["dogon"] = DOGON_GAMES
        prediction["bet"] = bet_amount
        prediction["closed_at"] = now.isoformat()

        telegram_edit(
            prediction.get("message_id"),
            make_result_message(prediction, "lose"),
        )

        apply_lose(prediction, bet_amount)

        print("", flush=True)
        print(f"❌ ПРОГНОЗ НЕ ЗАШЁЛ #N{target}", flush=True)
        print(
            f"🎯 Карта: {prediction['predicted_card']}",
            flush=True,
        )
        print(
            f"🔎 Проверено у дилера: {checked_games}",
            flush=True,
        )

        changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА ИГР TELEGRAM
# =====================================================================

def on_game_message(game_number, text, is_edited):

    # ---------------------------------------------------------------
    # PENDING
    # ---------------------------------------------------------------

    if game_number in pending_games:

        pending_games[game_number]["text"] = text

        print(
            f"🔄 Обновлена pending #N{game_number}",
            flush=True,
        )

        return

    # ---------------------------------------------------------------
    # CACHE
    # ---------------------------------------------------------------

    if game_number in games_cache:

        game = parse_game_message(text)

        if not game:
            return

        games_cache[game_number] = game

        print(f"🔄 Обновлена #N{game_number}", flush=True)

        return

    # ---------------------------------------------------------------
    # МАРКЕР ЗАВЕРШЕНИЯ
    # ---------------------------------------------------------------

    has_marker = bool(re.search(r"[✅🔰▶️◀️]", text))

    if has_marker:

        pending_games[game_number] = {
            "first_seen": time.time(),
            "text": text,
        }

        print(f"👀 #N{game_number} → pending", flush=True)


# =====================================================================
# ФИНАЛИЗАЦИЯ
# =====================================================================

def finalize_pending_games():

    now = time.time()
    ready = []

    for (game_number, info) in list(pending_games.items()):

        first_seen = info.get("first_seen", now)

        if now - first_seen >= FINALIZE_WAIT_SECONDS:
            ready.append(game_number)

    for game_number in ready:

        info = pending_games.pop(game_number, None)

        if not info:
            continue

        text = info.get("text", "")
        game = parse_game_message(text)

        if not game:

            print(
                f"⚠️ #N{game_number} не удалось разобрать",
                flush=True,
            )

            continue

        games_cache[game_number] = game
        log_game(game)

        # -----------------------------------------------------------
        # СОН
        # -----------------------------------------------------------

        if is_sleep_time():

            print(
                f"😴 #N{game_number}: сон — прогноз не создаём",
                flush=True,
            )

            continue

        # -----------------------------------------------------------
        # ПОСЛЕДНИЙ ПРОГНОЗ
        # -----------------------------------------------------------

        if is_last_prediction_time():

            print(
                f"🌙 #N{game_number}: после 22:30 — прогноз не создаём",
                flush=True,
            )

            continue

        # -----------------------------------------------------------
        # СОЗДАЁМ ПРОГНОЗ
        # -----------------------------------------------------------

        create_prediction(game)


# =====================================================================
# ОЧИСТКА КЭША
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


# =====================================================================
# ОЧИСТКА ПРОГНОЗОВ
# =====================================================================

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
    print("🚀 CYBER 21 — J/Q/K/A → 10 PREDICTOR", flush=True)
    print("==================================================", flush=True)
    print("📡 Игры: CHANNEL_STATS", flush=True)
    print("📤 Прогнозы: CHANNEL_PROGNOZ", flush=True)
    print(
        f"⏳ Финализация игры: {FINALIZE_WAIT_SECONDS} сек",
        flush=True,
    )
    print("🎯 Алгоритм: J/Q/K/A → 10", flush=True)
    print("⏳ Сдвиг: количество карт × 10", flush=True)
    print(f"🔄 Догоны: Д0..Д{DOGON_GAMES}", flush=True)
    print(
        f"⏰ Таймаут → возврат: {PREDICTION_TIMEOUT_MINUTES} мин",
        flush=True,
    )

    print("", flush=True)
    print("⏰ Расписание:", flush=True)
    print(
        f"   😴 Сон: "
        f"{SLEEP_HOUR:02d}:{SLEEP_MINUTE:02d} — "
        f"{WAKE_HOUR:02d}:{WAKE_MINUTE:02d}",
        flush=True,
    )
    print(
        f"   🌙 Последний прогноз до: "
        f"{LAST_PREDICTION_HOUR:02d}:"
        f"{LAST_PREDICTION_MINUTE:02d}",
        flush=True,
    )
    print(
        f"   🧹 Ночная очистка: "
        f"{CLEANUP_HOUR:02d}:"
        f"{CLEANUP_MINUTE:02d}",
        flush=True,
    )
    print("==================================================", flush=True)

    delete_webhook()
    load_bank()
    load_predictions()

    offset = load_offset()

    print(f"📌 Telegram offset: {offset}", flush=True)
    print(
        f"📊 Загружено прогнозов: {len(predictions)}",
        flush=True,
    )
    print("==================================================", flush=True)
    print("🟢 БОТ ГОТОВ", flush=True)
    print("==================================================", flush=True)

    while True:

        try:

            if should_cleanup_now():
                cleanup_nightly()

            offset = process_telegram_updates(
                offset,
                on_game_message,
            )

            finalize_pending_games()
            check_predictions()
            cleanup_games_cache()
            cleanup_predictions()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:

            print("\n🛑 Бот остановлен", flush=True)
            break

        except Exception as e:

            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(3)


# =====================================================================
# СТАРТ
# =====================================================================

if __name__ == "__main__":
    import threading
    import time

    threading.Thread(
        target=main,
        daemon=True,
    ).start()

    from web_server import start_web_server
    start_web_server()

    # Главный поток живёт вечно, пока работают daemon-потоки
    while True:
        time.sleep(60)
