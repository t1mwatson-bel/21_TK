# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import time
from datetime import datetime, timedelta, time as dtime

from config import (
    BOT_TOKEN, CHANNEL_PROGNOZ, CHANNEL_STATS,
    PREDICTIONS_FILE, POLL_INTERVAL, FINALIZE_WAIT_SECONDS,
    DOGON_GAMES, SEND_BEFORE_TARGET, PREDICTION_TIMEOUT_MINUTES,
    CLEANUP_HOUR, CLEANUP_MINUTE,
    MAX_GAMES_CACHE, MAX_PREDICTIONS_STORED, MOSCOW_TZ,
)

from parsers import (
    parse_game_message, log_game, add_game_offset,
    find_trigger, find_suit_in_player, normalize_suit,
)

from telegram_api import (
    delete_webhook, telegram_send, telegram_edit,
    process_telegram_updates, load_offset,
)

from bank import (
    load_bank, get_current_bet, bet_for_dogon,
    apply_win, apply_lose, apply_return,
)


# =====================================================================
# ENV-ПРОВЕРКА
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
# ГЛОБАЛЬНЫЕ
# =====================================================================

games_cache = {}
pending_games = {}
predictions = []
last_cleanup_date = None

# --- очистка predictions.json ---
last_predictions_cleanup = None
PREDICTIONS_RETENTION_HOURS = 48
PREDICTIONS_CLEANUP_INTERVAL_HOURS = 48


# =====================================================================
# ОЧИСТКА
# =====================================================================

def should_cleanup_now(now=None):
    global last_cleanup_date

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    if last_cleanup_date == now.date():
        return False

    return now.time() >= dtime(CLEANUP_HOUR, CLEANUP_MINUTE)


def cleanup_nightly():
    global games_cache, pending_games, last_cleanup_date

    print("\n🧹 НОЧНАЯ ОЧИСТКА", flush=True)

    games_cache.clear()
    pending_games.clear()

    last_cleanup_date = datetime.now(MOSCOW_TZ).date()

    print("✅ Готово\n", flush=True)


# =====================================================================
# ОЧИСТКА PREDICTIONS.JSON (раз в 48 часов, старше 48 часов)
# =====================================================================

def should_cleanup_predictions_now(now=None):
    global last_predictions_cleanup

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    if last_predictions_cleanup is None:
        return True

    return (now - last_predictions_cleanup) >= timedelta(
        hours=PREDICTIONS_CLEANUP_INTERVAL_HOURS
    )


def cleanup_old_predictions():
    global predictions, last_predictions_cleanup

    now = datetime.now(MOSCOW_TZ)
    cutoff = now - timedelta(hours=PREDICTIONS_RETENTION_HOURS)

    kept = []
    removed = 0

    for p in predictions:
        # pending не трогаем никогда — иначе потеряем активные прогнозы
        if p.get("status") == "pending":
            kept.append(p)
            continue

        ts_str = p.get("closed_at") or p.get("created_at")

        if not ts_str:
            kept.append(p)
            continue

        try:
            ts = datetime.fromisoformat(ts_str)
        except Exception:
            kept.append(p)
            continue

        if ts >= cutoff:
            kept.append(p)
        else:
            removed += 1

    predictions = kept
    last_predictions_cleanup = now

    if removed:
        save_predictions()

    print(
        f"🧹 Очистка прогнозов: удалено {removed}, осталось {len(predictions)}",
        flush=True
    )


# =====================================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ ПРОГНОЗОВ
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
        print(f"⚠️ Ошибка чтения прогнозов: {e}", flush=True)
        predictions = []


def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:
        print(f"🔥 Ошибка сохранения прогнозов: {e}", flush=True)


# =====================================================================
# СООБЩЕНИЯ
# =====================================================================

def make_prediction_message(p):
    return (
        f"🎯 Игра: <b>#N{p['target_number']}</b>: "
        f"{normalize_suit(p['predicted_suit'])}"
    )


def make_result_message(p, result):
    suit = normalize_suit(p["predicted_suit"])
    dogon = p.get("dogon", 0)

    if result == "win":
        mark = f" ✅ (Догон {dogon})"
    elif result == "lose":
        mark = " ❌"
    elif result == "return":
        mark = " ♻️"
    else:
        mark = ""

    return f"🎯 Игра: <b>#N{p['target_number']}</b>: {suit}{mark}"


# =====================================================================
# АКТИВНЫЙ ПРОГНОЗ
# =====================================================================

def has_active_prediction():
    for p in predictions:
        if p.get("status") == "pending":
            return True

    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(game):
    if has_active_prediction():
        return None

    trigger = find_trigger(game)

    if not trigger:
        return None

    target = trigger["target_number"]
    suit = trigger["predicted_suit"]
    base_bet = get_current_bet()

    prediction = {
        "algorithm": "player_first_card_trigger",
        "trigger_number": trigger["trigger_number"],
        "trigger_game_id": game.get("game_id"),
        "trigger_card": trigger["trigger_card"],
        "target_number": target,
        "predicted_suit": suit,
        "target_offset": target - trigger["trigger_number"],
        "send_at_game": target - SEND_BEFORE_TARGET,
        "dogon": 0,
        "base_bet": base_bet,
        "bet": base_bet,
        "status": "pending",
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

    print(
        f"\n🔮 ПРОГНОЗ: #N{target} {suit} "
        f"(триггер #N{trigger['trigger_number']} "
        f"{trigger['trigger_card']}, "
        f"отправка от #N{prediction['send_at_game']})",
        flush=True
    )

    return prediction


def send_prediction(p):
    mid = telegram_send(make_prediction_message(p))

    if not mid:
        print(f"❌ Не отправить #N{p['target_number']}", flush=True)
        return False

    p["message_id"] = mid
    p["sent_at"] = datetime.now(MOSCOW_TZ).isoformat()

    save_predictions()

    print(
        f"📤 ОТПРАВЛЕН #N{p['target_number']} "
        f"{p['predicted_suit']}",
        flush=True
    )

    return True


# =====================================================================
# ОТЛОЖЕННАЯ ОТПРАВКА
# =====================================================================

def try_send_pending():
    changed = False

    for p in predictions:
        if p.get("status") != "pending":
            continue

        if p.get("message_id"):
            continue

        send_at = p.get("send_at_game")

        if send_at is None:
            continue

        if not any(n >= send_at for n in games_cache):
            continue

        if send_prediction(p):
            changed = True

    if changed:
        save_predictions()


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def check_predictions():
    changed = False
    now = datetime.now(MOSCOW_TZ)

    for p in predictions:
        if p.get("status") != "pending":
            continue

        target = p.get("target_number")

        if not target:
            continue

        # Таймаут 20 минут от создания
        created_at_str = p.get("created_at")

        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)

                if now - created_at > timedelta(
                    minutes=PREDICTION_TIMEOUT_MINUTES
                ):
                    p["status"] = "return"
                    p["close_reason"] = "timeout"
                    p["closed_at"] = now.isoformat()

                    if p.get("message_id"):
                        telegram_edit(
                            p.get("message_id"),
                            make_result_message(p, "return")
                        )

                    apply_return(p, p.get("bet", 0))

                    print(
                        f"♻️ RETURN #N{target} "
                        f"(timeout {PREDICTION_TIMEOUT_MINUTES} мин)",
                        flush=True
                    )

                    changed = True
                    continue

            except Exception:
                pass

        waiting = False
        won = False
        checked = []

        base_bet = p.get("base_bet", p.get("bet", 0))

        # Проверка Д0–Д3
        for dogon in range(0, DOGON_GAMES + 1):
            gnum = add_game_offset(target, dogon)
            g = games_cache.get(gnum)

            if not g:
                waiting = True
                break

            checked.append(gnum)

            card = find_suit_in_player(
                g,
                p["predicted_suit"]
            )

            if card:
                bet = bet_for_dogon(base_bet, dogon)

                p["status"] = "win"
                p["result_game"] = gnum
                p["found_card"] = card
                p["dogon"] = dogon
                p["bet"] = bet
                p["closed_at"] = now.isoformat()

                if p.get("message_id"):
                    telegram_edit(
                        p.get("message_id"),
                        make_result_message(p, "win")
                    )

                apply_win(p, dogon, bet)

                print(
                    f"✅ WIN #N{target} на Д{dogon} ({card})",
                    flush=True
                )

                changed = True
                won = True
                break

        if won or waiting:
            continue

        # Полный проигрыш серии
        total = sum(
            bet_for_dogon(base_bet, d)
            for d in range(0, DOGON_GAMES + 1)
        )

        last_bet = bet_for_dogon(
            base_bet,
            DOGON_GAMES
        )

        p["status"] = "lose"
        p["result_game"] = (
            checked[-1]
            if checked
            else add_game_offset(target, DOGON_GAMES)
        )
        p["dogon"] = DOGON_GAMES
        p["bet"] = last_bet
        p["total_lost"] = total
        p["closed_at"] = now.isoformat()

        if p.get("message_id"):
            telegram_edit(
                p.get("message_id"),
                make_result_message(p, "lose")
            )

        # Передаём базовую ставку Д0, а не сумму серии
        apply_lose(p, base_bet)

        print(
            f"❌ LOSE #N{target} "
            f"(потеря серии {total} ₽, "
            f"последний догон {last_bet} ₽)",
            flush=True
        )

        changed = True

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА СООБЩЕНИЙ ИЗ КАНАЛА
# =====================================================================

def on_game_message(game_number, text, is_edited):
    if game_number in pending_games:
        pending_games[game_number]["text"] = text
        return

    if game_number in games_cache:
        g = parse_game_message(text)

        if g:
            games_cache[game_number] = g
            print(f"🔄 Обновлена #N{game_number}", flush=True)

        return

    if re.search(r"[✅🔰▶️◀️]", text):
        pending_games[game_number] = {
            "first_seen": time.time(),
            "text": text,
        }

        print(f"👀 #N{game_number} → pending", flush=True)


# =====================================================================
# ФИНАЛИЗАЦИЯ PENDING
# =====================================================================

def finalize_pending_games():
    now = time.time()

    ready = [
        n for n, i in pending_games.items()
        if now - i.get("first_seen", now) >= FINALIZE_WAIT_SECONDS
    ]

    for n in ready:
        info = pending_games.pop(n, None)

        if not info:
            continue

        g = parse_game_message(info.get("text", ""))

        if not g:
            print(f"⚠️ #N{n} не распарсился", flush=True)
            continue

        games_cache[n] = g
        log_game(g)
        create_prediction(g)


# =====================================================================
# ЧИСТКА КЭШЕЙ
# =====================================================================

def cleanup_games_cache():
    if len(games_cache) <= MAX_GAMES_CACHE:
        return

    items = sorted(
        games_cache.items(),
        key=lambda kv: kv[0]
    )

    for n, _ in items[:-MAX_GAMES_CACHE]:
        del games_cache[n]


def cleanup_predictions():
    global predictions

    if len(predictions) > MAX_PREDICTIONS_STORED:
        predictions = predictions[-MAX_PREDICTIONS_STORED:]
        save_predictions()


# =====================================================================
# ГЛАВНЫЙ ЦИКЛ
# =====================================================================

def main():
    print("\n==================================================", flush=True)
    print("🚀 CYBER 21 — PLAYER FIRST CARD TRIGGER", flush=True)
    print("==================================================", flush=True)
    print("🎯 Триггер: 1-я карта игрока → масть", flush=True)
    print("📍 Отступ до цели: +10 игр", flush=True)
    print(
        f"📤 Отправка за {SEND_BEFORE_TARGET} игр до цели",
        flush=True
    )
    print(f"🔄 Догоны: Д0..Д{DOGON_GAMES}", flush=True)
    print(
        f"⏰ Таймаут: {PREDICTION_TIMEOUT_MINUTES} мин от создания",
        flush=True
    )
    print("🔒 Один активный прогноз", flush=True)
    print(
        f"🧹 Очистка прогнозов: раз в {PREDICTIONS_CLEANUP_INTERVAL_HOURS} ч, "
        f"хранить {PREDICTIONS_RETENTION_HOURS} ч",
        flush=True
    )
    print("==================================================", flush=True)

    delete_webhook()
    load_bank()
    load_predictions()

    offset = load_offset()

    print(
        f"📌 offset: {offset} | прогнозов: {len(predictions)}",
        flush=True
    )
    print("🟢 БОТ ГОТОВ\n", flush=True)

    while True:
        try:
            if should_cleanup_now():
                cleanup_nightly()

            if should_cleanup_predictions_now():
                cleanup_old_predictions()

            offset = process_telegram_updates(
                offset,
                on_game_message
            )

            finalize_pending_games()
            try_send_pending()
            check_predictions()
            cleanup_games_cache()
            cleanup_predictions()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Остановлен", flush=True)
            break

        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(3)


# =====================================================================
# СТАРТ
# =====================================================================

if __name__ == "__main__":
    import threading

    threading.Thread(
        target=main,
        daemon=True
    ).start()

    from web_server import start_web_server

    start_web_server()
