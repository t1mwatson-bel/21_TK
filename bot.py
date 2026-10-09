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
    build_prediction_v2,
    find_card_in_game,
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
# КОНСТАНТЫ
# =====================================================================

# ВАЖНО:
# Цель теперь НЕ определяется через +1442.
#
# Триггер сегодня:
#   #N500
#
# Цель:
#   #N500 следующего игрового дня.
#
# Игровой день начинается в 03:00 МСК.

GAME_CYCLE = 1440

EXTRA_SEND_BEFORE = 7

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
# ИГРОВОЙ ДЕНЬ
# =====================================================================

def get_game_day(now=None):
    """
    Возвращает игровой день.

    Игровые сутки начинаются в 03:00 МСК.

    Например:

    2026-10-08 02:59 МСК
        -> игровой день 2026-10-07

    2026-10-08 03:00 МСК
        -> игровой день 2026-10-08
    """

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    if now.tzinfo is None:
        now = MOSCOW_TZ.localize(now)

    cleanup_time = dtime(CLEANUP_HOUR, CLEANUP_MINUTE)

    if now.time() < cleanup_time:
        return (now.date() - timedelta(days=1)).isoformat()

    return now.date().isoformat()


def parse_game_day(value):
    """
    Преобразует YYYY-MM-DD в date.
    """

    if not value:
        return None

    try:
        return datetime.strptime(
            str(value),
            "%Y-%m-%d",
        ).date()

    except Exception:
        return None


def game_day_plus(game_day, days):
    """
    Прибавляет дни к игровому дню.

    Возвращает строку YYYY-MM-DD.
    """

    date_value = parse_game_day(game_day)

    if date_value is None:
        return None

    return (date_value + timedelta(days=days)).isoformat()


def game_day_for_offset(base_day, base_number, offset):
    """
    Определяет игровой день конкретной игры после offset.

    Например:

    target = #1439
    D0 -> #1439, тот же день
    D1 -> #1440, тот же день
    D2 -> #1, следующий игровой день
    """

    base_number = int(base_number)
    offset = int(offset)

    raw_position = (base_number - 1) + offset

    day_shift = raw_position // GAME_CYCLE

    game_day = parse_game_day(base_day)

    if game_day is None:
        return None

    result_day = game_day + timedelta(days=day_shift)

    return result_day.isoformat()


def send_game_day_for_target(target_day, target_number):
    """
    Определяет игровой день игры, за которой нужно отправить прогноз.

    Например:

    цель #100
    отправка #93
    отправка в тот же игровой день.

    цель #3
    отправка #1436
    отправка происходит в предыдущий игровой день.
    """

    target_day_date = parse_game_day(target_day)

    if target_day_date is None:
        return None

    target_number = int(target_number)

    send_number = add_game_offset(
        target_number,
        -EXTRA_SEND_BEFORE,
    )

    # Если target #1..#7,
    # то семь игр назад мы попадаем в предыдущий игровой день.
    if target_number <= EXTRA_SEND_BEFORE:
        target_day_date -= timedelta(days=1)

    return target_day_date.isoformat()


def is_current_game_day(game_day):
    """
    Проверяет, является ли game_day текущим игровым днём.
    """

    return game_day == get_game_day()


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
    global games_cache
    global finalized_games
    global pending_games
    global predictions
    global last_cleanup_date

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

        with open(
            PREDICTIONS_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        predictions = data if isinstance(data, list) else []

    except Exception as e:
        print(
            f"⚠️ Ошибка чтения {PREDICTIONS_FILE}: {e}",
            flush=True,
        )

        predictions = []


def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                predictions,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            tmp,
            PREDICTIONS_FILE,
        )

    except Exception as e:
        print(
            f"⚠️ Ошибка сохранения прогнозов: {e}",
            flush=True,
        )


# =====================================================================
# АКТИВНЫЙ ПРОГНОЗ
# =====================================================================

def has_active_prediction():

    for prediction in predictions:

        if prediction.get("status") in (
            "pending",
            "scheduled",
        ):
            return True

    return False


# =====================================================================
# ФОРМАТ СООБЩЕНИЙ
# =====================================================================

def make_prediction_message(prediction):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    return (
        f"🎯 Игра: <b>#N{target}</b>: {card}"
    )


def make_result_message(
    prediction,
    result,
    found_card=None,
):

    target = prediction["target_number"]
    card = prediction["predicted_card"]

    if result == "win":

        where = (
            found_card.get("where")
            if found_card
            else None
        )

        if where == "dealer":
            where_text = " (дилер)"

        elif where == "player":
            where_text = " (игрок)"

        else:
            where_text = ""

        return (
            f"🎯 Игра: <b>#N{target}</b>: "
            f"{card} ✅{where_text}"
        )

    elif result == "lose":

        return (
            f"🎯 Игра: <b>#N{target}</b>: "
            f"{card} ❌"
        )

    elif result == "return":

        return (
            f"🎯 Игра: <b>#N{target}</b>: "
            f"{card} ♻️"
        )

    return (
        f"🎯 Игра: <b>#N{target}</b>: "
        f"{card} ⚠️"
    )


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

    groups = re.findall(
        r"\(([^()]*)\)",
        text,
    )

    if len(groups) < 2:
        return True

    card_pattern = re.compile(
        r"[2-9AJQK10][♠♣♦♥]"
    )

    if not card_pattern.search(groups[0]):
        return True

    if not card_pattern.search(groups[1]):
        return True

    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(trigger_game):

    trigger = find_trigger_v2(trigger_game)

    if not trigger:
        return None

    trigger_number = trigger_game["game_number"]

    trigger_id = trigger_game.get(
        "game_id"
    )

    rank = trigger["rank"]

    # ---------------------------------------------------------------
    # ИГРОВОЙ ДЕНЬ ТРИГГЕРА
    # ---------------------------------------------------------------

    trigger_day = trigger_game.get(
        "game_day"
    )

    if not trigger_day:
        trigger_day = get_game_day()

    # ---------------------------------------------------------------
    # МАСТЬ ИЗ ИГРЫ ТРИГГЕР - 3
    # ---------------------------------------------------------------

    suit_game_number = add_game_offset(
        trigger_number,
        -3,
    )

    suit_game = games_cache.get(
        suit_game_number
    )

    if not suit_game:

        print(
            f"⏳ Триггер #N{trigger_number} ({rank}) — "
            f"ждём игру #N{suit_game_number} для масти",
            flush=True,
        )

        return None

    prediction_data = build_prediction_v2(
        trigger_game,
        suit_game,
    )

    if not prediction_data:
        return None

    predicted_card = prediction_data[
        "predicted_card"
    ]

    predicted_rank = prediction_data[
        "predicted_rank"
    ]

    predicted_suit = prediction_data[
        "predicted_suit"
    ]

    base_bet = get_current_bet()

    # ---------------------------------------------------------------
    # ЦЕЛЬ = ТА ЖЕ ПОЗИЦИЯ СЛЕДУЮЩЕГО ИГРОВОГО ДНЯ
    # ---------------------------------------------------------------

    target_day = game_day_plus(
        trigger_day,
        1,
    )

    if not target_day:
        return None

    target_number = add_game_offset(trigger_number, 2)

    # ---------------------------------------------------------------
    # ИГРА, ПРИ КОТОРОЙ ОТПРАВЛЯЕМ ПРОГНОЗ
    # ---------------------------------------------------------------

    send_game_number = add_game_offset(
        target_number,
        -EXTRA_SEND_BEFORE,
    )

    send_game_day = send_game_day_for_target(
        target_day,
        target_number,
    )

    if not send_game_day:
        return None

    # ---------------------------------------------------------------
    # ПРОВЕРКА ДУБЛЯ
    # ---------------------------------------------------------------

    for old in predictions:

        if old.get("status") not in (
            "pending",
            "scheduled",
        ):
            continue

        if (
            old.get("target_day") == target_day
            and old.get("target_number") == target_number
            and old.get("predicted_card")
            == predicted_card
        ):
            return None

    # ---------------------------------------------------------------
    # СОЗДАЁМ ПРОГНОЗ
    # ---------------------------------------------------------------

    prediction = {

        "algorithm":
            "first_player_card_v2_next_day",

        # ТРИГГЕР
        "trigger_number":
            trigger_number,

        "trigger_game_id":
            trigger_id,

        "trigger_day":
            trigger_day,

        "trigger_card":
            trigger["trigger_card"],

        # ИГРА ДЛЯ МАСТИ
        "suit_game_number":
            suit_game_number,

        "suit_game_day":
            trigger_day,

        # ПРОГНОЗ
        "predicted_rank":
            predicted_rank,

        "predicted_suit":
            predicted_suit,

        "predicted_card":
            predicted_card,

        # ЦЕЛЬ
        "target_offset":
            GAME_CYCLE,

        "target_number":
            target_number,

        "target_day":
            target_day,

        # ОТПРАВКА
        "send_game_number":
            send_game_number,

        "send_game_day":
            send_game_day,

        # БАНК
        "base_bet":
            base_bet,

        # СТАТУС
        "status":
            "scheduled",

        "dogon":
            0,

        # ВРЕМЯ
        "created_at":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),

        "sent_at":
            None,

        "closed_at":
            None,

        "message_id":
            None,

        "result_game":
            None,

        "result_game_day":
            None,

        "found_card":
            None,

        "close_reason":
            None,
    }

    predictions.append(
        prediction
    )

    save_predictions()

    print("", flush=True)

    print(
        "📌 ПРОГНОЗ СОЗДАН "
        "(цель на следующий игровой день)",
        flush=True,
    )

    print(
        f"📌 Триггер: "
        f"#N{trigger_number}",
        flush=True,
    )

    print(
        f"📅 День триггера: "
        f"{trigger_day}",
        flush=True,
    )

    print(
        f"🃏 Первая карта: "
        f"{trigger['trigger_card']}",
        flush=True,
    )

    print(
        f"🎨 Масть из "
        f"#N{suit_game_number}: "
        f"{predicted_suit}",
        flush=True,
    )

    print(
        f"🎯 Прогноз: "
        f"{predicted_card}",
        flush=True,
    )

    print(
        f"🎯 ЦЕЛЬ ЗАВТРА: "
        f"#N{target_number}",
        flush=True,
    )

    print(
        f"📅 День цели: "
        f"{target_day}",
        flush=True,
    )

    print(
        f"📤 Отправка: "
        f"#N{send_game_number}",
        flush=True,
    )

    print(
        f"📅 День отправки: "
        f"{send_game_day}",
        flush=True,
    )

    return prediction


# =====================================================================
# ОТПРАВКА ПРОГНОЗА
# =====================================================================

def send_prediction(prediction):

    message = make_prediction_message(
        prediction
    )

    message_id = telegram_send(
        message
    )

    if not message_id:

        print(
            f"❌ Не удалось отправить "
            f"#N{prediction['target_number']}",
            flush=True,
        )

        return False

    prediction["message_id"] = (
        message_id
    )

    prediction["sent_at"] = (
        datetime.now(
            MOSCOW_TZ
        ).isoformat()
    )

    prediction["status"] = "pending"

    apply_dogon_bet(
        prediction,
        0,
    )

    save_predictions()

    print(
        f"📤 ПРОГНОЗ ОТПРАВЛЕН: "
        f"#N{prediction['target_number']} "
        f"{prediction['predicted_card']}",
        flush=True,
    )

    print(
        f"📅 Целевой игровой день: "
        f"{prediction.get('target_day')}",
        flush=True,
    )

    return True


# =====================================================================
# ОТПРАВКА ЗАПЛАНИРОВАННЫХ ПРОГНОЗОВ
# =====================================================================

def send_scheduled_predictions():

    changed = False

    current_day = get_game_day()

    for prediction in predictions:

        if prediction.get("status") != "scheduled":
            continue

        target_day = prediction.get(
            "target_day"
        )

        send_day = prediction.get(
            "send_game_day"
        )

        send_number = prediction.get(
            "send_game_number"
        )

        if not target_day or not send_day:
            continue

        if not send_number:
            continue

        # -----------------------------------------------------------
        # Ещё не наступил день отправки
        # -----------------------------------------------------------

        if current_day < send_day:
            continue

        # -----------------------------------------------------------
        # Если мы уже ушли дальше нужного дня,
        # прогноз лучше не отправлять задним числом.
        # -----------------------------------------------------------

        if current_day > target_day:
            print(
                f"⚠️ Пропущена отправка "
                f"#N{prediction.get('target_number')} "
                f"(день цели уже прошёл)",
                flush=True,
            )
            continue

        # -----------------------------------------------------------
        # Проверяем, что нужная игра отправки уже пришла
        # -----------------------------------------------------------

        if send_day == current_day:

            if (
                send_number not in finalized_games
                and send_number not in games_cache
            ):
                continue

        # -----------------------------------------------------------
        # Для редкого случая, когда отправка происходит
        # в предыдущий игровой день.
        # -----------------------------------------------------------

        elif send_day < current_day:

            # Игра уже должна была пройти.
            # Отправляем прогноз сразу.
            pass

        else:
            continue

        print(
            f"📤 Отправка прогноза "
            f"#N{prediction['target_number']} "
            f"(цель: {target_day}, "
            f"отправка: #N{send_number})",
            flush=True,
        )

        if send_prediction(
            prediction
        ):
            changed = True

    if changed:
        save_predictions()


# =====================================================================
# ФИНАЛИЗАЦИЯ ИГР
# =====================================================================

def finalize_pending_games():

    now = time.time()

    ready = []

    for (
        game_number,
        info,
    ) in list(
        pending_games.items()
    ):

        last_update = info.get(
            "last_update",
            info.get(
                "first_seen",
                now,
            ),
        )

        if (
            now - last_update
            >= FINALIZE_WAIT_SECONDS
        ):
            ready.append(
                game_number
            )

    for game_number in ready:

        info = pending_games.pop(
            game_number,
            None,
        )

        if not info:
            continue

        text = info.get(
            "text",
            "",
        )

        game = parse_game_message(
            text
        )

        if not game:
            continue

        # Сохраняем игровой день
        game["game_day"] = info.get(
            "game_day",
            get_game_day(),
        )

        finalized_games[
            game_number
        ] = game

        print(
            f"✅ #N{game_number} "
            f"→ finalized "
            f"({game['game_day']})",
            flush=True,
        )


# =====================================================================
# ПОЛУЧЕНИЕ ИГРЫ ДЛЯ КОНКРЕТНОГО ДНЯ
# =====================================================================

def get_finalized_game(
    game_number,
    game_day,
):
    """
    Возвращает игру только если она относится
    к нужному игровому дню.

    Это важно, потому что #N1 существует
    каждый игровой день.
    """

    if not game_day:
        return None

    current_day = get_game_day()

    if game_day != current_day:
        return None

    game = finalized_games.get(
        game_number
    )

    if not game:
        return None

    stored_day = game.get(
        "game_day"
    )

    if stored_day != game_day:
        return None

    return game


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def check_predictions():

    changed = False

    now = datetime.now(
        MOSCOW_TZ
    )

    current_day = get_game_day()

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get(
            "target_number"
        )

        target_day = prediction.get(
            "target_day"
        )

        if target is None:
            continue

        if not target_day:
            continue

        # -----------------------------------------------------------
        # Проверяем только когда наступил игровой день цели
        # -----------------------------------------------------------

        if current_day < target_day:
            continue

        # -----------------------------------------------------------
        # Если день цели уже полностью прошёл,
        # дальше проверять нечего.
        # -----------------------------------------------------------

        if current_day > target_day:
            # Но если догоны перешли на следующий игровой день,
            # разрешаем продолжить проверку ниже.
            last_dogon_day = game_day_for_offset(
                target_day,
                target,
                DOGON_GAMES,
            )

            if (
                last_dogon_day
                and current_day < last_dogon_day
            ):
                continue

        # -----------------------------------------------------------
        # ТАЙМАУТ
        #
        # ВАЖНО:
        # Таймаут считается только после фактической отправки.
        # Мы больше НЕ ждём 23 часа.
        # -----------------------------------------------------------

        sent_at_str = prediction.get(
            "sent_at"
        )

        if sent_at_str:

            try:

                sent_at = datetime.fromisoformat(
                    sent_at_str
                )

                if (
                    now - sent_at
                    > timedelta(
                        minutes=PREDICTION_TIMEOUT_MINUTES
                    )
                ):

                    # НО:
                    # Нельзя возвращать прогноз просто потому,
                    # что целевая игра ещё не пришла.
                    #
                    # Проверяем, существует ли уже
                    # нужная игра в потоке.

                    target_game = get_finalized_game(
                        target,
                        target_day,
                    )

                    if target_game:

                        # Только если целевая игра действительно
                        # уже завершена и есть все необходимые данные,
                        # тогда таймаут имеет смысл.

                        pass

            except Exception:
                pass

        # -----------------------------------------------------------
        # ПОИСК КАРТЫ
        # -----------------------------------------------------------

        current_dogon = prediction.get(
            "dogon",
            0,
        )

        won = False
        found_card = None
        win_dogon = None

        for dogon_index in range(
            0,
            DOGON_GAMES + 1,
        ):

            game_number = add_game_offset(
                target,
                dogon_index,
            )

            game_day = game_day_for_offset(
                target_day,
                target,
                dogon_index,
            )

            game = get_finalized_game(
                game_number,
                game_day,
            )

            if not game:
                continue

            found = find_card_in_game(
                game,
                prediction[
                    "predicted_card"
                ],
            )

            if found:

                won = True

                found_card = found

                win_dogon = dogon_index

                break

        # -----------------------------------------------------------
        # WIN
        # -----------------------------------------------------------

        if won:

            result_game = add_game_offset(
                target,
                win_dogon,
            )

            result_game_day = game_day_for_offset(
                target_day,
                target,
                win_dogon,
            )

            prediction["status"] = "win"

            prediction["result_game"] = (
                result_game
            )

            prediction["result_game_day"] = (
                result_game_day
            )

            prediction["found_card"] = (
                found_card
            )

            prediction["dogon"] = (
                win_dogon
            )

            prediction["closed_at"] = (
                now.isoformat()
            )

            telegram_edit(
                prediction.get(
                    "message_id"
                ),
                make_result_message(
                    prediction,
                    "win",
                    found_card,
                ),
            )

            apply_win(
                prediction,
                win_dogon,
                found_card,
            )

            print(
                "",
                flush=True,
            )

            print(
                f"✅ ПРОГНОЗ ЗАШЁЛ "
                f"#N{target}",
                flush=True,
            )

            print(
                f"📅 День: "
                f"{result_game_day}",
                flush=True,
            )

            print(
                f"🎯 Карта: "
                f"{prediction['predicted_card']}",
                flush=True,
            )

            print(
                f"🃏 Найдена: "
                f"{found_card['card']} "
                f"({found_card['where']})",
                flush=True,
            )

            print(
                f"🔄 Догон: "
                f"Д{win_dogon}",
                flush=True,
            )

            changed = True

            continue

        # -----------------------------------------------------------
        # ПРОВЕРЯЕМ, ЗАКОНЧИЛСЯ ЛИ УЖЕ ВЕСЬ ДИАПАЗОН Д0..Д3
        # -----------------------------------------------------------

        all_finalized = True

        for dogon_index in range(
            0,
            DOGON_GAMES + 1,
        ):

            game_number = add_game_offset(
                target,
                dogon_index,
            )

            game_day = game_day_for_offset(
                target_day,
                target,
                dogon_index,
            )

            game = get_finalized_game(
                game_number,
                game_day,
            )

            if not game:

                all_finalized = False

                break

        if not all_finalized:
            continue

        # -----------------------------------------------------------
        # LOSE / ПЕРЕХОД НА СЛЕДУЮЩИЙ ДОН
        # -----------------------------------------------------------

        if current_dogon < DOGON_GAMES:

            next_dogon = (
                current_dogon + 1
            )

            apply_lose(
                prediction,
                current_dogon,
            )

            prediction["dogon"] = (
                next_dogon
            )

            print(
                f"❌ Д{current_dogon} проиграл, "
                f"переходим на Д{next_dogon}",
                flush=True,
            )

            changed = True

            continue

        # -----------------------------------------------------------
        # FINAL LOSE
        # -----------------------------------------------------------

        apply_lose(
            prediction,
            current_dogon,
        )

        prediction["status"] = "lose"

        prediction["closed_at"] = (
            now.isoformat()
        )

        telegram_edit(
            prediction.get(
                "message_id"
            ),
            make_result_message(
                prediction,
                "lose",
            ),
        )

        print(
            "",
            flush=True,
        )

        print(
            f"❌ ПРОГНОЗ НЕ ЗАШЁЛ "
            f"#N{target}",
            flush=True,
        )

        changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА ИГР TELEGRAM
# =====================================================================

def on_game_message(
    game_number,
    text,
    is_edited,
):

    if is_waiting_message(text):
        return

    game = parse_game_message(
        text
    )

    if not game:
        return

    if (
        not game.get("player_cards")
        or not game.get("dealer_cards")
    ):
        return

    # ---------------------------------------------------------------
    # ВАЖНО:
    # Фиксируем игровой день именно в момент
    # получения игры.
    # ---------------------------------------------------------------

    current_game_day = get_game_day()

    game["game_day"] = (
        current_game_day
    )

    is_new = (
        game_number
        not in games_cache
    )

    games_cache[
        game_number
    ] = game

    pending_games[
        game_number
    ] = {
        "text": text,
        "last_update": time.time(),
        "first_seen": time.time(),
        "game_day": current_game_day,
    }

    log_game(game)

    # ВСЕГДА пробуем создать прогноз
    create_prediction(game)


# =====================================================================
# ОЧИСТКА GAMES CACHE
# =====================================================================

def cleanup_games_cache():

    if (
        len(games_cache)
        <= MAX_GAMES_CACHE
    ):
        return

    items = sorted(
        games_cache.items(),
        key=lambda kv:
            kv[1].get(
                "received_at",
                "",
            ),
    )

    for number, _ in items[
        :-MAX_GAMES_CACHE
    ]:

        del games_cache[
            number
        ]


# =====================================================================
# ОЧИСТКА FINALIZED
# =====================================================================

def cleanup_finalized_games():

    if (
        len(finalized_games)
        <= MAX_GAMES_CACHE
    ):
        return

    items = sorted(
        finalized_games.items(),
        key=lambda kv:
            kv[1].get(
                "received_at",
                "",
            ),
    )

    for number, _ in items[
        :-MAX_GAMES_CACHE
    ]:

        del finalized_games[
            number
        ]


# =====================================================================
# ОЧИСТКА ПРОГНОЗОВ
# =====================================================================

def cleanup_predictions():

    global predictions

    if (
        len(predictions)
        > MAX_PREDICTIONS_STORED
    ):

        predictions = predictions[
            -MAX_PREDICTIONS_STORED:
        ]

        save_predictions()


# =====================================================================
# ГЛАВНЫЙ ЦИКЛ
# =====================================================================

def main():

    global predictions

    print(
        "",
        flush=True,
    )

    print(
        "==================================================",
        flush=True,
    )

    print(
        "🚀 CYBER 21 — FIRST CARD PREDICTOR v2",
        flush=True,
    )

    print(
        "==================================================",
        flush=True,
    )

    print(
        "📡 Игры: CHANNEL_STATS",
        flush=True,
    )

    print(
        "📤 Прогнозы: CHANNEL_PROGNOZ",
        flush=True,
    )

    print(
        "🎯 Алгоритм: первая карта игрока J/Q/K/A",
        flush=True,
    )

    print(
        "🎯 Масть: от игры триггер − 3",
        flush=True,
    )

    print(
        "📅 Цель: ТА ЖЕ ИГРА СЛЕДУЮЩЕГО ИГРОВОГО ДНЯ",
        flush=True,
    )

    print(
        f"📤 Отправка: за "
        f"{EXTRA_SEND_BEFORE} игр до цели",
        flush=True,
    )

    print(
        "⏰ Игровой день: 03:00 МСК",
        flush=True,
    )

    print(
        "💸 Ставок на прогноз: 2 "
        "(игрок + дилер)",
        flush=True,
    )

    print(
        f"🔄 Догоны: "
        f"Д0..Д{DOGON_GAMES}",
        flush=True,
    )

    print(
        f"⏰ Таймаут: "
        f"{PREDICTION_TIMEOUT_MINUTES} мин",
        flush=True,
    )

    print(
        f"⏳ Финализация игры: "
        f"{FINALIZE_WAIT_SECONDS} сек",
        flush=True,
    )

    print(
        "==================================================",
        flush=True,
    )

    delete_webhook()

    load_bank()

    load_predictions()

    offset = load_offset()

    print(
        f"📌 Telegram offset: "
        f"{offset}",
        flush=True,
    )

    print(
        f"📊 Загружено прогнозов: "
        f"{len(predictions)}",
        flush=True,
    )

    print(
        f"📅 Текущий игровой день: "
        f"{get_game_day()}",
        flush=True,
    )

    print(
        "==================================================",
        flush=True,
    )

    print(
        "🟢 БОТ ГОТОВ",
        flush=True,
    )

    print(
        "==================================================",
        flush=True,
    )

    while True:

        try:

            # -------------------------------------------------------
            # НОЧНАЯ ОЧИСТКА
            # -------------------------------------------------------

            if should_cleanup_now():
                cleanup_nightly()

            # -------------------------------------------------------
            # ПОЛУЧАЕМ ИГРЫ
            # -------------------------------------------------------

            offset = process_telegram_updates(
                offset,
                on_game_message,
            )

            # -------------------------------------------------------
            # ФИНАЛИЗАЦИЯ
            # -------------------------------------------------------

            finalize_pending_games()

            # -------------------------------------------------------
            # ОТПРАВКА ЗАПЛАНИРОВАННЫХ
            # -------------------------------------------------------

            send_scheduled_predictions()

            # -------------------------------------------------------
            # ПРОВЕРКА ПРОГНОЗОВ
            # -------------------------------------------------------

            check_predictions()

            # -------------------------------------------------------
            # ОЧИСТКА
            # -------------------------------------------------------

            cleanup_games_cache()

            cleanup_finalized_games()

            cleanup_predictions()

            time.sleep(
                POLL_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 Бот остановлен",
                flush=True,
            )

            break

        except Exception as e:

            print(
                f"❌ Критическая ошибка: "
                f"{e}",
                flush=True,
            )

            import traceback

            traceback.print_exc()

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
