
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

PREDICTION_TIMEOUT_MINUTES = 300
FINALIZE_WAIT_SECONDS = 30
PENDING_CHECK_TIMEOUT_MINUTES = 5

# Фиксированные ставки D0-D6.
DOGON_BETS = (50, 75, 113, 170, 255, 383, 575)
MAX_DOGON_INDEX = 6

# Цель должна быть ровно на +10 игр от триггера.
REQUIRED_TARGET_OFFSET = 10

# Публикуем за 2 игры до целевой.
REQUIRED_PUBLISH_OFFSET = 2


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


def calculate_bet_amount(base_bet, dogon_index):
    """Возвращает фиксированную ставку D0-D6."""
    try:
        index = int(dogon_index)
    except (ValueError, TypeError):
        index = 0

    index = max(0, min(index, MAX_DOGON_INDEX))
    return DOGON_BETS[index]


def get_max_dogon():
    """Не допускаем выход за пределы D6."""
    try:
        configured = int(DOGON_GAMES)
    except (ValueError, TypeError):
        configured = MAX_DOGON_INDEX

    return max(0, min(configured, MAX_DOGON_INDEX))


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
    global games_cache
    global finalized_games
    global pending_games
    global last_cleanup_date

    now = now_moscow()

    print("🧹 НОЧНАЯ ОЧИСТКА", flush=True)

    # Не удаляем активные прогнозы и их состояние.
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
            json.dump(
                predictions,
                f,
                ensure_ascii=False,
                indent=2
            )
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


def has_active_prediction():
    active_statuses = (
        "pending_check",
        "pending_publish",
        "pending",
        "pending_edit",
    )

    return any(
        p.get("status") in active_statuses
        for p in predictions
    )


def find_r_anomaly(game_number):
    game = get_game(game_number)

    if not game:
        return None

    return bool(game.get("is_r"))


# =====================================================================
# СООБЩЕНИЯ TELEGRAM
# =====================================================================

def make_prediction_message(prediction, dogon_index):
    target = prediction["target_number"]
    rank = prediction["predicted_rank"]

    bet_amount = calculate_bet_amount(
        prediction.get("base_bet", 50),
        dogon_index
    )

    return (
        f"🎯 Игра: <b>#N{target}</b>: "
        f"{rank} ставка {bet_amount}₽"
    )


def make_result_message(
    prediction,
    result,
    dogon_index,
    found_card=None
):
    target = prediction["target_number"]
    rank = prediction["predicted_rank"]

    bet_amount = calculate_bet_amount(
        prediction.get("base_bet", 50),
        dogon_index
    )

    balance = int(math.ceil(get_balance()))

    if result == "win":
        return (
            f"🎯 Игра: <b>#N{target}</b>: "
            f"{rank} ✅ при {bet_amount}₽\n"
            f"🤑 Баланс: {balance}₽"
        )

    if result == "lose":
        return (
            f"🎯 Игра: <b>#N{target}</b>: "
            f"{rank} ❌\n"
            f"🤑 Баланс: {balance}₽"
        )

    if result == "return":
        return (
            f"🎯 Игра: <b>#N{target}</b>: "
            f"{rank} ♻️\n"
            f"🤑 Баланс: {balance}₽"
        )

    return f"🎯 Игра: <b>#N{target}</b>: {rank} ⚠️"


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

    trigger = find_trigger_21(game)

    if not trigger:
        return None

    trigger_number = game.get("game_number")

    if trigger_number is None:
        return None

    try:
        trigger_number = int(trigger_number)
    except (ValueError, TypeError):
        return None

    predicted_rank = trigger.get("predicted_rank")

    if not predicted_rank:
        return None

    # Настройки config.py не меняем.
    # Защищаем расчёт от случайно неверных значений.
    target_offset = REQUIRED_TARGET_OFFSET
    publish_offset = REQUIRED_PUBLISH_OFFSET

    # Проверяем предыдущий номер с учётом игрового цикла.
    before_number = add_game_offset(trigger_number, -1)
    after_number = add_game_offset(trigger_number, 1)

    before_game = get_game(before_number)

    # Если предыдущая игра доступна и в ней есть #R,
    # прогноз не создаём.
    if before_game and before_game.get("is_r"):
        print(
            f"⛔ #N{trigger_number}: #R перед триггером, пропуск",
            flush=True
        )
        return None

    target_number = add_game_offset(
        trigger_number,
        target_offset
    )

    publish_number = add_game_offset(
        target_number,
        -publish_offset
    )

    for old in predictions:
        if old.get("trigger_number") == trigger_number:
            if old.get("status") not in (
                "cancelled",
                "lose",
                "win",
                "return",
            ):
                return None

    # Для этой последовательности первая ставка всегда 50 ₽.
    base_bet = DOGON_BETS[0]

    prediction = {
        "algorithm": "cyber21_rank_v4",

        "trigger_number": trigger_number,
        "trigger_game_id": game.get("game_id"),
        "trigger_card": trigger.get("trigger_card"),
        "predicted_rank": predicted_rank,
        "cf_player": get_player_cf(predicted_rank),

        "target_offset": target_offset,
        "target_number": target_number,
        "publish_number": publish_number,

        "base_bet": base_bet,
        "bet": base_bet,

        "status": "pending_check",
        "dogon": 0,

        "created_at": now_moscow().isoformat(),
        "sent_at": None,
        "closed_at": None,

        "message_id": None,
        "pending_publish": False,

        "result_game": None,
        "found_card": None,
        "close_reason": None,

        "check_before_game_number": before_number,
        "check_game_number": after_number,

        "before_r_checked": False,
        "after_r_checked": False,

        "last_publish_attempt": None,
    }

    predictions.append(prediction)
    save_predictions()

    print(
        f"🔮 СОЗДАН #N{trigger_number} -> "
        f"Цель #{target_number} "
        f"(Публикация #{publish_number})",
        flush=True
    )

    return prediction


# =====================================================================
# ПУБЛИКАЦИЯ ПРОГНОЗА
# =====================================================================

def send_prediction(prediction):
    """
    Публикует прогноз.

    Если message_id не получен, сохраняет pending_publish.
    Следующая итерация основного цикла повторит попытку.
    """

    if prediction.get("message_id"):
        return True

    dogon_index = prediction.get("dogon", 0)

    message = make_prediction_message(
        prediction,
        dogon_index
    )

    prediction["status"] = "pending_publish"
    prediction["pending_publish"] = True
    prediction["last_publish_attempt"] = now_moscow().isoformat()

    # Сохраняем состояние до сетевого вызова.
    save_predictions()

    try:
        response = telegram_send(message)
        message_id = normalize_message_id(response)

    except Exception as e:
        print(
            f"⚠️ Ошибка публикации #{prediction['target_number']}: {e}",
            flush=True
        )

        save_predictions()
        return False

    if not message_id:
        prediction["status"] = "pending_publish"
        prediction["pending_publish"] = True
        prediction["message_id"] = None

        save_predictions()

        print(
            f"⚠️ Telegram не вернул message_id для "
            f"#{prediction['target_number']}; "
            "повторная попытка будет выполнена",
            flush=True
        )

        return False

    prediction["message_id"] = message_id
    prediction["sent_at"] = now_moscow().isoformat()
    prediction["status"] = "pending"
    prediction["pending_publish"] = False

    prediction["dogon"] = dogon_index
    prediction["bet"] = calculate_bet_amount(
        prediction["base_bet"],
        dogon_index
    )

    apply_dogon_bet(prediction, dogon_index)

    save_state()

    print(
        f"📤 ОТПРАВЛЕН #{prediction['target_number']} "
        f"(ставка {prediction['bet']}₽)",
        flush=True
    )

    return True


# =====================================================================
# ПРОВЕРКА #R ДО И ПОСЛЕ ТРИГГЕРА
# =====================================================================

def resolve_pending_checks():
    now = now_moscow()
    changed = False

    for p in predictions:
        if p.get("status") != "pending_check":
            continue

        before_num = p.get("check_before_game_number")
        after_num = p.get("check_game_number")

        before_game = get_game(before_num)
        after_game = get_game(after_num)

        # Проверяем игру перед триггером.
        if before_game:
            p["before_r_checked"] = True

            if before_game.get("is_r"):
                p["status"] = "cancelled"
                p["close_reason"] = "anomaly_before"
                p["closed_at"] = now.isoformat()

                print(
                    f"⛔ ОТМЕНА #{p['trigger_number']}: "
                    "#R перед триггером",
                    flush=True
                )

                changed = True
                continue

        # Проверяем игру после триггера.
        if after_game:
            p["after_r_checked"] = True

            if after_game.get("is_r"):
                p["status"] = "cancelled"
                p["close_reason"] = "anomaly_after"
                p["closed_at"] = now.isoformat()

                print(
                    f"⛔ ОТМЕНА #{p['trigger_number']}: "
                    "#R после триггера",
                    flush=True
                )

                changed = True
                continue

        # Публикация разрешена только после обеих проверок.
        if (
            p.get("before_r_checked")
            and p.get("after_r_checked")
        ):
            p["status"] = "pending_publish"
            p["pending_publish"] = True

            print(
                f"✅ #N{p['trigger_number']}: "
                "обе проверки #R пройдены",
                flush=True
            )

            changed = True
            continue

        created = parse_datetime(p.get("created_at"), now)

        if now - created > timedelta(
            minutes=PENDING_CHECK_TIMEOUT_MINUTES
        ):
            p["status"] = "cancelled"
            p["close_reason"] = "check_timeout"
            p["closed_at"] = now.isoformat()

            print(
                f"⛔ ОТМЕНА #{p['trigger_number']}: "
                "не удалось завершить обе проверки #R",
                flush=True
            )

            changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОЖИДАЮЩИЕ ПУБЛИКАЦИИ
# =====================================================================

def resolve_pending_publishes():
    now = now_moscow()

    all_numbers = (
        list(games_cache.keys())
        + list(finalized_games.keys())
    )

    latest_game = max(all_numbers) if all_numbers else 0

    for p in predictions:
        if p.get("status") != "pending_publish":
            continue

        # Повторяем отправку, если message_id не был получен.
        if p.get("pending_publish") and not p.get("message_id"):
            if latest_game >= p.get("publish_number", 0):
                send_prediction(p)
            continue

        pub_num = p.get("publish_number")

        if latest_game >= pub_num:
            send_prediction(p)
            continue

        created = parse_datetime(p.get("created_at"), now)

        if now - created > timedelta(
            minutes=PREDICTION_TIMEOUT_MINUTES
        ):
            p["status"] = "cancelled"
            p["close_reason"] = "publish_timeout"
            p["closed_at"] = now.isoformat()

            print(
                f"⛔ ОТМЕНА #{p['trigger_number']}: "
                "таймаут публикации",
                flush=True
            )

            save_predictions()


# =====================================================================
# ФИНАЛИЗАЦИЯ ИГР
# =====================================================================

def finalize_pending_games():
    current_time = time.time()

    ready = [
        num
        for num, info in pending_games.items()
        if current_time - info.get(
            "last_update",
            current_time
        ) >= FINALIZE_WAIT_SECONDS
    ]

    for num in ready:
        info = pending_games.pop(num, None)

        if not info:
            continue

        game = parse_game_message(info.get("text", ""))

        if game:
            finalized_games[num] = game


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ И ДОГОНОВ
# =====================================================================

def check_predictions():
    changed = False
    now = now_moscow()

    for p in predictions:
        if p.get("status") != "pending":
            continue

        target = p.get("target_number")
        current_dogon = int(p.get("dogon", 0))

        # Номер игры с учётом игрового цикла.
        game_number = add_game_offset(
            target,
            current_dogon
        )

        game = finalized_games.get(game_number)

        if not game:
            sent_at = parse_datetime(p.get("sent_at"), now)

            if now - sent_at > timedelta(
                minutes=PREDICTION_TIMEOUT_MINUTES
            ):
                p["status"] = "return"
                p["closed_at"] = now.isoformat()
                p["result_game"] = game_number

                apply_return(p, p.get("bet", 0))

                if p.get("message_id"):
                    telegram_edit(
                        p["message_id"],
                        make_result_message(
                            p,
                            "return",
                            current_dogon
                        )
                    )

                print(
                    f"♻️ Возврат #{target}: "
                    f"игра #{game_number} не получена",
                    flush=True
                )

                changed = True

            continue

        p["result_game"] = game_number

        found = find_rank_in_player(
            game,
            p["predicted_rank"]
        )

        # -------------------------------------------------------------
        # ВЫИГРЫШ
        # -------------------------------------------------------------

        if found:
            p["status"] = "win"
            p["dogon"] = current_dogon
            p["found_card"] = found
            p["closed_at"] = now.isoformat()

            # Сначала применяем выигрыш, потом формируем баланс.
            apply_win(
                p,
                current_dogon,
                found
            )

            if p.get("message_id"):
                telegram_edit(
                    p["message_id"],
                    make_result_message(
                        p,
                        "win",
                        current_dogon,
                        found
                    )
                )

            print(
                f"✅ ПРОГНОЗ #{target} ЗАШЁЛ "
                f"на D{current_dogon}",
                flush=True
            )

            changed = True
            continue

        # -------------------------------------------------------------
        # ПРОИГРЫШ И ПЕРЕХОД К СЛЕДУЮЩЕМУ ДОГОНУ
        # -------------------------------------------------------------

        max_dogon = get_max_dogon()

        if current_dogon < max_dogon:
            next_dogon = current_dogon + 1

            # Списываем текущую ставку (внутри bank.py уже спишется следующая)
            apply_lose(p, current_dogon)

            p["dogon"] = next_dogon
            p["bet"] = calculate_bet_amount(
                p["base_bet"],
                next_dogon
            )
            p["sent_at"] = now.isoformat()

            # УБРАЛИ apply_dogon_bet(p, next_dogon), так как apply_lose уже это сделала!

            # Редактируем исходное сообщение.
            if p.get("message_id"):
                telegram_edit(
                    p["message_id"],
                    make_prediction_message(
                        p,
                        next_dogon
                    )
                )

            print(
                f"❌ D{current_dogon} проиграл, "
                f"D{next_dogon} "
                f"(ставка {p['bet']}₽)",
                flush=True
            )

            changed = True
            continue

        # -------------------------------------------------------------
        # ПРОИГРЫШ НА D6 — ЗАВЕРШЕНИЕ
        # -------------------------------------------------------------

        apply_lose(
            p,
            current_dogon
        )

        p["status"] = "lose"
        p["closed_at"] = now.isoformat()

        if p.get("message_id"):
            telegram_edit(
                p["message_id"],
                make_result_message(
                    p,
                    "lose",
                    current_dogon
                )
            )

        print(
            f"❌ ПРОГНОЗ #{target} ПРОИГРАН "
            "(все догоны D0-D6)",
            flush=True
        )

        changed = True

    if changed:
        save_state()


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
        oldest = sorted(games_cache.keys())[
            :len(games_cache) - MAX_GAMES_CACHE
        ]

        for num in oldest:
            del games_cache[num]

    if len(finalized_games) > MAX_GAMES_CACHE:
        oldest = sorted(finalized_games.keys())[
            :len(finalized_games) - MAX_GAMES_CACHE
        ]

        for num in oldest:
            del finalized_games[num]

    if len(predictions) > MAX_PREDICTIONS_STORED:
        # Не удаляем активные прогнозы.
        active = [
            p for p in predictions
            if p.get("status") in (
                "pending_check",
                "pending_publish",
                "pending",
                "pending_edit",
            )
        ]

        inactive = [
            p for p in predictions
            if p.get("status") not in (
                "pending_check",
                "pending_publish",
                "pending",
                "pending_edit",
            )
        ]

        keep_count = max(
            0,
            MAX_PREDICTIONS_STORED - len(active)
        )

        predictions[:] = inactive[-keep_count:] + active \
            if keep_count else active

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

            offset = process_telegram_updates(
                offset,
                on_game_message
            )

            finalize_pending_games()
            resolve_pending_checks()
            resolve_pending_publishes()
            check_predictions()
            cleanup_caches()

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(
                f"❌ Ошибка основного цикла: {e}",
                flush=True
            )

            time.sleep(3)


# =====================================================================
# ЗАПУСК
# =====================================================================

if __name__ == "__main__":
    import threading

    threading.Thread(
        target=main,
        daemon=True
    ).start()

    from web_server import start_web_server

    start_web_server()

    while True:
        time.sleep(60)
