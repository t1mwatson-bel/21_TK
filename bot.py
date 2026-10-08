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
    find_cards_in_game,
    get_extra_card,
    normalize_suit,
    card_to_text,
    cards_to_text,
)

from coefs import get_dealer_cf, get_player_cf

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
# РАСПИСАНИЕ СНА (отключено)
# =====================================================================

def is_sleep_time(now=None):
    return False


def is_last_prediction_time(now=None):
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

        if prediction.get("status") in ("pending", "preparing"):
            return True

    return False


# =====================================================================
# ФОРМАТ СООБЩЕНИЙ
# =====================================================================

def make_prediction_message(prediction):

    target = prediction["target_number"]

    cards = prediction.get("predicted_cards", [])

    cards_text = " / ".join(cards)

    return f"🎯 Игра: <b>#N{target}</b>: {cards_text}"


def make_result_message(prediction, result, found_info=None):

    target = prediction["target_number"]

    cards = prediction.get("predicted_cards", [])

    cards_text = " / ".join(cards)

    if result == "win":

        won_texts = []

        if found_info:
            for item in found_info:
                card = item.get("card")
                where = item.get("where")

                if where == "dealer":
                    won_texts.append(f"{card} (дилер)")
                elif where == "player":
                    won_texts.append(f"{card} (игрок)")
                else:
                    won_texts.append(card)

        won_text = " + ".join(won_texts) if won_texts else "✅"

        return f"🎯 Игра: <b>#N{target}</b>: {cards_text} ✅ {won_text}"

    elif result == "lose":
        return f"🎯 Игра: <b>#N{target}</b>: {cards_text} ❌"

    elif result == "return":
        return f"🎯 Игра: <b>#N{target}</b>: {cards_text} ♻️"

    return f"🎯 Игра: <b>#N{target}</b>: {cards_text} ⚠️"


# =====================================================================
# ПРЕДУПРЕЖДЕНИЕ
# =====================================================================

def send_upcoming_warning(prediction):

    target = prediction["target_number"]

    cards = prediction.get("predicted_cards", [])

    cards_text = " / ".join(cards)

    message = (
        f"⏳ <b>Скоро прогноз!</b>\n"
        f"🎯 Цель: <b>#N{target}</b>\n"
        f"🃏 Карты: <b>{cards_text}</b>\n"
        f"⏱ Готовимся..."
    )

    message_id = telegram_send(message)

    if message_id:

        prediction["warning_message_id"] = message_id
        save_predictions()

        print(
            f"⚠️ ПРЕДУПРЕЖДЕНИЕ ОТПРАВЛЕНО: #N{target} {cards_text}",
            flush=True,
        )

    else:

        print(
            f"❌ Не удалось отправить предупреждение #N{target}",
            flush=True,
        )


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(game):

    if has_active_prediction():
        return None

    trigger = find_trigger(game)

    if not trigger:
        return None

    trigger_number = game["game_number"]
    trigger_id = game.get("game_id")

    predicted_card_main = trigger["predicted_card"]

    # Дополнительная карта (парная масть)
    predicted_card_extra = get_extra_card(predicted_card_main)

    if not predicted_card_extra:
        print(
            f"⚠️ Не удалось получить парную масть для "
            f"{predicted_card_main} — пропускаем",
            flush=True,
        )
        return None

    predicted_cards = [predicted_card_main, predicted_card_extra]

    player_card_count = trigger["player_card_count"]
    target_offset = trigger["target_offset"]

    target_number = add_game_offset(trigger_number, target_offset)

    # Проверка дубля
    for old in predictions:

        if old.get("status") not in ("pending", "preparing"):
            continue

        old_cards = old.get("predicted_cards", [])

        if (
            old.get("target_number") == target_number
            and old_cards == predicted_cards
        ):
            return None

    base_bet = get_current_bet()
    cf = get_dealer_cf(predicted_card_main)

    prediction = {

        "algorithm": "rank_before_10_plus_suit_10_double",

        "trigger_number": trigger_number,
        "trigger_game_id": trigger_id,
        "trigger_card": trigger["trigger_card"],
        "ten_card": trigger["ten_card"],

        "predicted_rank": trigger["predicted_rank"],
        "predicted_suit": trigger["predicted_suit"],

        "predicted_card": predicted_card_main,
        "predicted_card_extra": predicted_card_extra,
        "predicted_cards": predicted_cards,

        "cf": cf,

        "player_card_count": player_card_count,
        "target_offset": target_offset,
        "target_number": target_number,

        "base_bet": base_bet,
        "bet": base_bet,

        "status": "preparing",
        "warning_sent": False,
        "warning_message_id": None,
        "ready_to_send": False,

        "dogon": 0,
        "bets_placed": [0],

        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "sent_at": None,
        "closed_at": None,
        "message_id": None,
        "result_game": None,
        "found_cards": None,
        "close_reason": None,
    }

    predictions.append(prediction)
    save_predictions()

    warning_game = add_game_offset(target_number, -12)
    send_game = add_game_offset(target_number, -8)

    print("", flush=True)
    print("🔮 ПРОГНОЗ ПОДГОТОВЛЕН (ждём подход к цели)", flush=True)
    print(f"📌 Триггер: #N{trigger_number}", flush=True)
    print(
        f"🃏 Триггер: {trigger['trigger_card']} → {trigger['ten_card']}",
        flush=True,
    )
    print(
        f"🎯 Прогноз: {predicted_card_main} / {predicted_card_extra}",
        flush=True,
    )
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

    warning_id = prediction.get("warning_message_id")

    if warning_id:

        telegram_delete(warning_id)
        prediction["warning_message_id"] = None

    # Списываем ставку за Д0
    apply_dogon_bet(prediction, 0)

    prediction["bets_placed"] = [0]

    save_predictions()

    print(
        f"📤 ПРОГНОЗ ОТПРАВЛЕН: "
        f"#N{prediction['target_number']} "
        f"{' / '.join(prediction.get('predicted_cards', []))}",
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
        # ПОДГОТОВКА
        # -----------------------------------------------------------

        if status == "preparing":

            target = prediction.get("target_number")

            if not target:
                continue

            # -------------------------------------------------------
            # ТАЙМАУТ ДЛЯ PREPARING (70 минут)
            # -------------------------------------------------------

            created_at_str = prediction.get("created_at")

            if created_at_str:

                try:

                    created_at = datetime.fromisoformat(created_at_str)

                    if now - created_at > timedelta(minutes=70):

                        prediction["status"] = "return"
                        prediction["close_reason"] = "preparing_timeout"
                        prediction["closed_at"] = now.isoformat()

                        warning_id = prediction.get("warning_message_id")

                        if warning_id:
                            telegram_delete(warning_id)
                            prediction["warning_message_id"] = None

                        apply_return(prediction)

                        print(
                            f"♻️ ПРИНУДИТЕЛЬНЫЙ ВОЗВРАТ #N{target} "
                            f"(preparing > 70 минут)",
                            flush=True,
                        )

                        changed = True
                        continue

                except Exception:
                    pass

            if not prediction.get("warning_sent"):

                warning_game = add_game_offset(target, -12)

                if warning_game in games_cache:

                    send_upcoming_warning(prediction)
                    prediction["warning_sent"] = True
                    changed = True

            if not prediction.get("ready_to_send"):

                send_game = add_game_offset(target, -8)

                if send_game in games_cache:

                    prediction["status"] = "pending"
                    prediction["ready_to_send"] = True

                    send_prediction(prediction)
                    changed = True

            continue

        # -----------------------------------------------------------
        # PENDING
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
                    > timedelta(minutes=70)
                ):

                    prediction["status"] = "return"
                    prediction["close_reason"] = "timeout"
                    prediction["closed_at"] = now.isoformat()

                    telegram_edit(
                        prediction.get("message_id"),
                        make_result_message(prediction, "return"),
                    )

                    apply_return(prediction)

                    print(
                        f"♻️ ВОЗВРАТ #N{target} "
                        f"(timeout > 70 мин)",
                        flush=True,
                    )

                    changed = True
                    continue

            except Exception:
                pass

        # -----------------------------------------------------------
        # ТЕКУЩИЙ ДОГОН
        # -----------------------------------------------------------

        current_dogon = prediction.get("dogon")

        if current_dogon is None:
            current_dogon = 0

        # -----------------------------------------------------------
        # ПРОВЕРКА ИГРЫ НА ТЕКУЩЕМ ДОГОНЕ
        # -----------------------------------------------------------

        game_number = add_game_offset(target, current_dogon)
        game = games_cache.get(game_number)

        if not game:
            # Игра ещё не пришла
            continue

        # Ищем обе карты
        found_list = find_cards_in_game(
            game,
            prediction.get("predicted_cards", []),
        )

        # Отбираем только зашедшие
        found_cards = [
            item for item in found_list
            if item.get("where") is not None
        ]

        if found_cards:

            # Победа!
            prediction["status"] = "win"
            prediction["result_game"] = game_number
            prediction["found_cards"] = found_cards
            prediction["closed_at"] = now.isoformat()

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "win", found_cards),
            )

            apply_win(prediction, current_dogon, found_cards)

            print("", flush=True)
            print(f"✅ ПРОГНОЗ ЗАШЁЛ #N{target}", flush=True)
            print(
                f"🎯 Карты: {' / '.join(prediction.get('predicted_cards', []))}",
                flush=True,
            )
            for item in found_cards:
                print(
                    f"🃏 Найдена: {item['card']} "
                    f"({item['where']})",
                    flush=True,
                )
            print(f"🔄 Догон: Д{current_dogon}", flush=True)
            print(f"🎰 Игра: #N{game_number}", flush=True)

            changed = True
            continue

        # -----------------------------------------------------------
        # ПРОИГРЫШ НА ТЕКУЩЕМ ДОГОНЕ
        # -----------------------------------------------------------

        if current_dogon < DOGON_GAMES:

            # Переход на следующий догон
            next_dogon = current_dogon + 1

            apply_lose(prediction, current_dogon)

            prediction["dogon"] = next_dogon

            print(
                f"❌ Д{current_dogon} проиграл, "
                f"переходим на Д{next_dogon}",
                flush=True,
            )

            changed = True

        else:

            # Весь цикл проигран
            apply_lose(prediction, current_dogon)

            prediction["status"] = "lose"
            prediction["closed_at"] = now.isoformat()

            telegram_edit(
                prediction.get("message_id"),
                make_result_message(prediction, "lose"),
            )

            print("", flush=True)
            print(f"❌ ПРОГНОЗ НЕ ЗАШЁЛ #N{target}", flush=True)
            print(
                f"🎯 Карты: {' / '.join(prediction.get('predicted_cards', []))}",
                flush=True,
            )

            changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА ИГР TELEGRAM
# =====================================================================

def on_game_message(game_number, text, is_edited):

    if game_number in pending_games:

        pending_games[game_number]["text"] = text
        pending_games[game_number]["last_update"] = time.time()

        print(f"🔄 Обновлена pending #N{game_number}", flush=True)

        return

    if game_number in games_cache:

        game = parse_game_message(text)

        if not game:
            return

        games_cache[game_number] = game

        print(f"🔄 Обновлена #N{game_number}", flush=True)

        return

    has_marker = bool(re.search(r"[✅🔰▶️◀️]", text))

    if has_marker:

        pending_games[game_number] = {
            "first_seen": time.time(),
            "last_update": time.time(),
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

        last_update = info.get(
            "last_update",
            info.get("first_seen", now),
        )

        if now - last_update >= FINALIZE_WAIT_SECONDS:
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

        if is_sleep_time():
            continue

        if is_last_prediction_time():
            continue

        create_prediction(game)


# =====================================================================
# ОЧИСТКА
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
    print(f"⏳ Финализация игры: {FINALIZE_WAIT_SECONDS} сек", flush=True)
    print("🎯 Алгоритм: J/Q/K/A → 10", flush=True)
    print("🎯 Двойной прогноз: основная + парная масть", flush=True)
    print("💸 Ставок на прогноз: 4 (2 карты × 2 позиции)", flush=True)
    print(f"🔄 Догоны: Д0..Д{DOGON_GAMES}", flush=True)
    print(f"⏰ Таймаут → возврат: {PREDICTION_TIMEOUT_MINUTES} мин", flush=True)

    print("", flush=True)
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

    while True:
        time.sleep(60)