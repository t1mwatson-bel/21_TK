import os
import json

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    MOSCOW_TZ,
)

from coefs import get_dealer_cf, get_player_cf


# =====================================================================
# КОНСТАНТЫ ЛОГИКИ СТАВОК
# =====================================================================

# Шаг внутри цикла догонов: Д0=50, Д1=75, Д2=100, Д3=125
DOGON_STEP = 25.0

# Шаг между циклами: если все 4 проиграли, база растёт на 100
CYCLE_STEP = 100.0

# Количество догонов в одном цикле (Д0 + 3)
DOGONS_PER_CYCLE = 4

# Количество ставок на прогноз (2 карты × 2 позиции)
BETS_PER_PREDICTION = 4


# =====================================================================
# СОСТОЯНИЕ БАНКА (в памяти)
# =====================================================================

bank_state = {
    "balance": START_BALANCE,
    "current_bet": START_BET,
    "started_at": None,
    "history": [],
}


# =====================================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ
# =====================================================================

def load_bank():
    global bank_state

    try:
        if not os.path.exists(BANK_FILE):
            bank_state = {
                "balance": START_BALANCE,
                "current_bet": START_BET,
                "started_at": datetime.now(MOSCOW_TZ).isoformat(),
                "history": [],
            }
            save_bank()
            return

        with open(BANK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        bank_state = {
            "balance": float(data.get("balance", START_BALANCE)),
            "current_bet": float(data.get("current_bet", START_BET)),
            "started_at": data.get("started_at"),
            "history": data.get("history", []),
        }

    except Exception as e:
        print(f"⚠️ Ошибка чтения банка: {e}", flush=True)
        bank_state = {
            "balance": START_BALANCE,
            "current_bet": START_BET,
            "started_at": datetime.now(MOSCOW_TZ).isoformat(),
            "history": [],
        }


def save_bank():
    try:
        tmp = BANK_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bank_state, f, ensure_ascii=False, indent=2)

        os.replace(tmp, BANK_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения банка: {e}", flush=True)


# =====================================================================
# ГЕТТЕРЫ
# =====================================================================

def get_balance():
    return float(bank_state["balance"])


def get_current_bet():
    return float(bank_state["current_bet"])


# =====================================================================
# ЛОГИКА СТАВОК
# =====================================================================

def bet_for_dogon(base_bet, dogon_index):
    """
    Считает сумму ВСЕХ 4 ставок для конкретного догона.

    dogon_index: 0 = Д0, 1 = Д1, 2 = Д2, 3 = Д3.

    Пример при base_bet = 50:
        Д0: 4 × 50 = 200
        Д1: 4 × 75 = 300
        Д2: 4 × 100 = 400
        Д3: 4 × 125 = 500
    """

    one_bet = base_bet + dogon_index * DOGON_STEP

    return round(one_bet * BETS_PER_PREDICTION, 2)


def next_cycle_base(current_base):
    """База следующего цикла: current_base + 100."""

    return round(current_base + CYCLE_STEP, 2)


# =====================================================================
# СПИСАНИЕ СТАВКИ ПРИ ПЕРЕХОДЕ НА ДОГОН
# =====================================================================

def apply_dogon_bet(prediction, dogon_index):
    """
    Списывает сумму 4 ставок за конкретный догон.
    Вызывается, когда прогноз переходит на новый догон.
    """

    base_bet = prediction.get(
        "base_bet",
        bank_state["current_bet"],
    )

    amount = bet_for_dogon(base_bet, dogon_index)

    bank_state["balance"] = round(
        bank_state["balance"] - amount,
        2,
    )

    record = {
        "type": "bet",
        "game_number": prediction.get("target_number"),
        "dogon": dogon_index,
        "amount": amount,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💸 BET Д{dogon_index}: списано {amount} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


# =====================================================================
# ПРИМЕНЕНИЕ РЕЗУЛЬТАТА ПРОГНОЗА
# =====================================================================

def apply_win(prediction, dogon_index, found_cards):
    """
    Прогноз выиграл.

    found_cards — список зашедших карт с указанием, где нашли:
        [
            {"card": "K♦️", "where": "dealer"},
            {"card": "K♠️", "where": "player"},
        ]

    Начисляем выплату по каждой зашедшей ставке.
    """

    base_bet = prediction.get(
        "base_bet",
        bank_state["current_bet"],
    )

    one_bet = base_bet + dogon_index * DOGON_STEP

    total_payout = 0.0
    payouts = []

    for found in found_cards:

        card = found["card"]
        where = found["where"]

        if where == "dealer":
            cf = get_dealer_cf(card)
        elif where == "player":
            cf = get_player_cf(card)
        else:
            continue

        payout = round(one_bet * cf, 2)
        total_payout += payout

        payouts.append({
            "card": card,
            "where": where,
            "cf": cf,
            "bet": one_bet,
            "payout": payout,
        })

    bank_state["balance"] = round(
        bank_state["balance"] + total_payout,
        2,
    )

    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "predicted_cards": prediction.get("predicted_cards"),
        "dogon": dogon_index,
        "payouts": payouts,
        "total_payout": round(total_payout, 2),
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: выплата {total_payout} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_lose(prediction, dogon_index):
    """
    Прогноз проиграл на конкретном догоне.

    Если dogon_index < 3 — переходим на следующий догон.
    Если dogon_index == 3 — весь цикл проигран, увеличиваем базу на +100.
    """

    base_bet = prediction.get(
        "base_bet",
        bank_state["current_bet"],
    )

    if dogon_index < DOGONS_PER_CYCLE - 1:

        # Списываем ставку следующего догона
        next_index = dogon_index + 1
        apply_dogon_bet(prediction, next_index)

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "dogon": dogon_index,
            "next_dogon": next_index,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }

    else:

        # Весь цикл проигран — увеличиваем базу
        new_base = next_cycle_base(base_bet)
        bank_state["current_bet"] = new_base

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "dogon": dogon_index,
            "cycle_lost": True,
            "next_base": new_base,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"❌ LOSE Д{dogon_index} | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_return(prediction):
    """
    Возврат ♻️ (таймаут >30 мин).

    current_bet сбрасывается на START_BET.
    """

    bank_state["current_bet"] = START_BET

    record = {
        "type": "return",
        "game_number": prediction.get("target_number"),
        "predicted_cards": prediction.get("predicted_cards"),
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"♻️ ВОЗВРАТ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


# =====================================================================
# СТАТИСТИКА БАНКА (для сайта)
# =====================================================================

def get_bank_summary():

    balance = bank_state["balance"]
    profit = round(balance - START_BALANCE, 2)
    roi = round((profit / START_BALANCE) * 100, 2) if START_BALANCE else 0.0

    return {
        "balance": balance,
        "start_balance": START_BALANCE,
        "profit": profit,
        "roi": roi,
        "current_bet": bank_state["current_bet"],
        "started_at": bank_state.get("started_at"),
    }


def get_bank_history(limit=100):

    history = bank_state.get("history", [])
    return history[-limit:]