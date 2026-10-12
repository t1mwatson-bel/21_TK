import os
import json

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    MOSCOW_TZ,
)

from coefs import get_player_cf, get_banker_cf


# =====================================================================
# КОНСТАНТЫ ЛОГИКИ СТАВОК
# =====================================================================

DOGON_STEP = 25.0
CYCLE_STEP = 100.0
DOGONS_PER_CYCLE = 4

# 2 ставки: игрок + банкир
BETS_PER_PREDICTION = 2


# =====================================================================
# СОСТОЯНИЕ БАНКА
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
    Сумма 2 ставок (игрок + банкир) для конкретного догона.

    При base_bet = 50:
        Д0: 2 × 50 = 100
        Д1: 2 × 75 = 150
        Д2: 2 × 100 = 200
        Д3: 2 × 125 = 250
    """

    one_bet = base_bet + dogon_index * DOGON_STEP

    return round(one_bet * BETS_PER_PREDICTION, 2)


def next_cycle_base(current_base):
    """База следующего цикла: current_base + 100."""
    return round(current_base + CYCLE_STEP, 2)


# =====================================================================
# СПИСАНИЕ СТАВКИ
# =====================================================================

def apply_dogon_bet(prediction, dogon_index):

    base_bet = prediction.get("base_bet", bank_state["current_bet"])

    amount = bet_for_dogon(base_bet, dogon_index)

    bank_state["balance"] = round(bank_state["balance"] - amount, 2)

    record = {
        "type": "bet",
        "game_number": prediction.get("target_number"),
        "rank": prediction.get("predicted_rank"),
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
# РЕЗУЛЬТАТ: WIN
# =====================================================================

def apply_win(prediction, dogon_index, found):
    """
    Прогноз выиграл.

    found — словарь:
        {"card": "10♠️", "where": "player"} или
        {"card": "10♥️", "where": "dealer"}
    """

    base_bet = prediction.get("base_bet", bank_state["current_bet"])

    one_bet = base_bet + dogon_index * DOGON_STEP

    rank = prediction.get("predicted_rank")
    where = found.get("where")

    if where == "player":
        cf = get_player_cf(rank)
    elif where == "dealer":
        cf = get_banker_cf(rank)
    else:
        cf = 5.0

    payout = round(one_bet * cf, 2)

    bank_state["balance"] = round(bank_state["balance"] + payout, 2)

    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "rank": rank,
        "dogon": dogon_index,
        "card": found.get("card"),
        "where": where,
        "cf": cf,
        "bet": one_bet,
        "payout": payout,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: {found.get('card')} × {cf} → {payout} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


# =====================================================================
# РЕЗУЛЬТАТ: LOSE
# =====================================================================

def apply_lose(prediction, dogon_index):

    base_bet = prediction.get("base_bet", bank_state["current_bet"])

    if dogon_index < DOGONS_PER_CYCLE - 1:

        next_index = dogon_index + 1
        apply_dogon_bet(prediction, next_index)

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "rank": prediction.get("predicted_rank"),
            "dogon": dogon_index,
            "next_dogon": next_index,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }

    else:

        new_base = next_cycle_base(base_bet)
        bank_state["current_bet"] = new_base

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "rank": prediction.get("predicted_rank"),
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


# =====================================================================
# РЕЗУЛЬТАТ: RETURN
# =====================================================================

def apply_return(prediction, bet_amount=None):

    bank_state["current_bet"] = START_BET

    record = {
        "type": "return",
        "game_number": prediction.get("target_number"),
        "rank": prediction.get("predicted_rank"),
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
# СТАТИСТИКА
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