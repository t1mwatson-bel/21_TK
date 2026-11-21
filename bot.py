# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import time
import random
from datetime import datetime, timedelta, time as dtime

from config import (
    BOT_TOKEN, CHANNEL_PROGNOZ, CHANNEL_STATS,
    CHANNEL_PROGNOZ_2, SECOND_CHANNEL_CHANCE,
    PREDICTIONS_FILE, POLL_INTERVAL, FINALIZE_WAIT_SECONDS,
    DOGON_GAMES, SEND_BEFORE_TARGET, PREDICTION_TIMEOUT_MINUTES,
    CLEANUP_HOUR, CLEANUP_MINUTE,
    MAX_GAMES_CACHE, MAX_PREDICTIONS_STORED, MOSCOW_TZ,
    START_BET,
)

from parsers import (
    parse_game_message, log_game, add_game_offset,
    find_trigger, find_suit_in_player, normalize_suit,
    TriggerProcessor,
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
last_predictions_cleanup = None
PREDICTIONS_RETENTION_HOURS = 48
PREDICTIONS_CLEANUP_INTERVAL_HOURS = 48

trigger_processor = TriggerProcessor(
    on_trigger=lambda trig: create_prediction(trig)
)


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


def should_cleanup_predictions_now(now=None):
    global last_predictions_cleanup
    if now is None:
        now = datetime.now(MOSCOW_TZ)
    if last_predictions_cleanup is None:
        return True
    return (now - last_predictions_cleanup) >= timedelta(hours=PREDICTIONS_CLEANUP_INTERVAL_HOURS)


def cleanup_old_predictions():
    global predictions, last_predictions_cleanup
    now = datetime.now(MOSCOW_TZ)
    cutoff = now - timedelta(hours=PREDICTIONS_RETENTION_HOURS)
    kept = []
    removed = 0
    for p in predictions:
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
    print(f"🧹 Очистка прогнозов: удалено {removed}, осталось {len(predictions)}", flush=True)


# =====================================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ
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


def has_active_prediction():
    for p in predictions:
        if p.get("status") == "pending":
            return True
    return False


# =====================================================================
# ЭФФЕКТИВНАЯ СТАВКА
# =====================================================================

def get_effective_base_bet():
    base = get_current_bet()
    max_dogon = 0
    max_base = None
    for p in predictions:
        if p.get("status") != "pending":
            continue
        try:
            d = int(p.get("dogon", 0))
        except (TypeError, ValueError):
            continue
        if d > max_dogon:
            max_dogon = d
            max_base = p.get("base_bet")
    if max_dogon > 0 and max_base:
        try:
            return round(float(max_base) * (2.5 ** max_dogon), 2)
        except Exception:
            return base
    return base


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction(trigger):
    if not trigger:
        return None
    try:
        target = trigger["target_number"]
        suit = trigger["predicted_suit"]

        for p in predictions:
            if p.get("target_number") == target and p.get("status") == "pending":
                return None

        base_bet = get_effective_base_bet()
        if base_bet is None:
            base_bet = START_BET

        prediction = {
            "algorithm": "player_first_card_trigger",
            "trigger_number": trigger["trigger_number"],
            "trigger_game_id": trigger.get("trigger_game_id"),
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
            "message_id_2": None,
            "result_game": None,
            "found_card": None,
            "close_reason": None,
        }

        predictions.append(prediction)
        save_predictions()

        print(
            f"\n🔮 ПРОГНОЗ: #N{target} {suit} "
            f"(триггер #N{trigger['trigger_number']} {trigger['trigger_card']}, "
            f"ставка {base_bet} ₽, отправка от #N{prediction['send_at_game']})",
            flush=True,
        )
        return prediction
    except Exception as e:
        print(f"⚠️ Ошибка создания прогноза: {e}", flush=True)
        return None


def send_prediction(p):
    try:
        text = make_prediction_message(p)
        mid = telegram_send(text)

        if not mid:
            print(f"❌ Не отправить #N{p['target_number']}", flush=True)
            return False

        p["message_id"] = mid
        p["sent_at"] = datetime.now(MOSCOW_TZ).isoformat()

        # === ВТОРОЙ КАНАЛ: 30% шанс ===
        if CHANNEL_PROGNOZ_2 and random.random() < SECOND_CHANNEL_CHANCE:
            try:
                mid2 = telegram_send(text, chat_id=CHANNEL_PROGNOZ_2)
                if mid2:
                    p["message_id_2"] = mid2
                    print(f"📤 + копия во второй канал #N{p['target_number']}", flush=True)
            except Exception as e:
                print(f"⚠️ Ошибка отправки во второй канал: {e}", flush=True)

        save_predictions()
        print(f"📤 ОТПРАВЛЕН #N{p['target_number']} {p['predicted_suit']}", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка send_prediction: {e}", flush=True)
        return False


# =====================================================================
# ОТЛОЖЕННАЯ ОТПРАВКА
# =====================================================================

def try_send_pending():
    changed = False
    for p in predictions:
        try:
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
        except Exception as e:
            print(f"⚠️ Ошибка try_send_pending для #N{p.get('target_number')}: {e}", flush=True)
            continue
    if changed:
        save_predictions()


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ (с защитой от зависаний)
# =====================================================================

def check_predictions():
    changed = False
    now = datetime.now(MOSCOW_TZ)

    for p in predictions:
        try:
            if p.get("status") != "pending":
                continue

            target = p.get("target_number")
            if not target:
                continue

            # Таймаут
            created_at_str = p.get("created_at")
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str)
                    if now - created_at > timedelta(minutes=PREDICTION_TIMEOUT_MINUTES):
                        p["status"] = "return"
                        p["close_reason"] = "timeout"
                        p["closed_at"] = now.isoformat()

                        if p.get("message_id"):
                            telegram_edit(p["message_id"], make_result_message(p, "return"))
                        if p.get("message_id_2"):
                            telegram_edit(p["message_id_2"], make_result_message(p, "return"), chat_id=CHANNEL_PROGNOZ_2)

                        try:
                            apply_return(p, p.get("bet", 0))
                        except Exception as be:
                            print(f"⚠️ Ошибка банка (return #N{target}): {be}", flush=True)

                        print(f"♻️ RETURN #N{target} (timeout)", flush=True)
                        changed = True
                        continue
                except Exception:
                    pass

            waiting = False
            won = False
            checked = []

            base_bet = p.get("base_bet", p.get("bet", 0))
            if not base_bet:
                base_bet = START_BET

            for dogon in range(0, DOGON_GAMES + 1):
                gnum = add_game_offset(target, dogon)
                g = games_cache.get(gnum)
                if not g:
                    waiting = True
                    break
                checked.append(gnum)
                card = find_suit_in_player(g, p["predicted_suit"])
                if card:
                    bet = bet_for_dogon(base_bet, dogon)
                    p["status"] = "win"
                    p["result_game"] = gnum
                    p["found_card"] = card
                    p["dogon"] = dogon
                    p["bet"] = bet
                    p["closed_at"] = now.isoformat()

                    if p.get("message_id"):
                        telegram_edit(p["message_id"], make_result_message(p, "win"))
                    if p.get("message_id_2"):
                        telegram_edit(p["message_id_2"], make_result_message(p, "win"), chat_id=CHANNEL_PROGNOZ_2)

                    try:
                        apply_win(p, dogon, bet)
                    except Exception as be:
                        print(f"⚠️ Ошибка банка (win #N{target}): {be}", flush=True)

                    print(f"✅ WIN #N{target} на Д{dogon} ({card})", flush=True)
                    changed = True
                    won = True
                    break

            if won or waiting:
                continue

            # LOSE
            total = sum(bet_for_dogon(base_bet, d) for d in range(0, DOGON_GAMES + 1))
            last_bet = bet_for_dogon(base_bet, DOGON_GAMES)

            p["status"] = "lose"
            p["result_game"] = checked[-1] if checked else add_game_offset(target, DOGON_GAMES)
            p["dogon"] = DOGON_GAMES
            p["bet"] = last_bet
            p["total_lost"] = total
            p["closed_at"] = now.isoformat()

            if p.get("message_id"):
                telegram_edit(p["message_id"], make_result_message(p, "lose"))
            if p.get("message_id_2"):
                telegram_edit(p["message_id_2"], make_result_message(p, "lose"), chat_id=CHANNEL_PROGNOZ_2)

            try:
                apply_lose(p, base_bet)
            except Exception as be:
                print(f"⚠️ Ошибка банка (lose #N{target}): {be}", flush=True)

            print(f"❌ LOSE #N{target} (потеря серии {total} ₽)", flush=True)
            changed = True

        except Exception as e:
            print(f"🔥 Ошибка обработки прогноза #N{p.get('target_number')}: {e}", flush=True)
            # Чтобы не застрять на этом прогнозе навсегда — помечаем как broken
            try:
                p["status"] = "broken"
                p["close_reason"] = f"error: {e}"
                p["closed_at"] = now.isoformat()
                changed = True
            except Exception:
                pass
            continue

    if changed:
        save_predictions()


# =====================================================================
# ОБРАБОТКА ИГР
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


def finalize_pending_games():
    now = time.time()
    ready = [
        n for n, i in pending_games.items()
        if now - i.get("first_seen", now) >= FINALIZE_WAIT_SECONDS
    ]
    ready.sort()
    for n in ready:
        info = pending_games.pop(n, None)
        if not info:
            continue
        try:
            g = parse_game_message(info.get("text", ""))
            if not g:
                print(f"⚠️ #N{n} не распарсился", flush=True)
                continue
            games_cache[n] = g
            log_game(g)
            trigger_processor.feed(g)
        except Exception as e:
            print(f"⚠️ Ошибка финализации #N{n}: {e}", flush=True)


def cleanup_games_cache():
    if len(games_cache) <= MAX_GAMES_CACHE:
        return
    items = sorted(games_cache.items(), key=lambda kv: kv[0])
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
    print(f"📤 Основной канал: {CHANNEL_PROGNOZ}", flush=True)
    if CHANNEL_PROGNOZ_2:
        print(f"🎲 Второй канал: {CHANNEL_PROGNOZ_2} (шанс {int(SECOND_CHANNEL_CHANCE*100)}%)", flush=True)
    else:
        print("⚠️ Второй канал не задан (CHAT_ID_21_2)", flush=True)
    print("==================================================", flush=True)

    delete_webhook()
    load_bank()
    load_predictions()

    offset = load_offset()
    print(f"📌 offset: {offset} | прогнозов: {len(predictions)}", flush=True)
    print("🟢 БОТ ГОТОВ\n", flush=True)

    while True:
        try:
            if should_cleanup_now():
                cleanup_nightly()
            if should_cleanup_predictions_now():
                cleanup_old_predictions()

            offset = process_telegram_updates(offset, on_game_message)

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


if __name__ == "__main__":
    import threading

    threading.Thread(target=main, daemon=True).start()

    from web_server import start_web_server
    start_web_server()