import os
import json
import math

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    MOSCOW_TZ,
)

from coefs import get_player_cf


# =====================================================================
# КОНСТАНТЫ ЛОГИКИ СТАВОК (21)
# =====================================================================

# 7 догонов: Д0, Д1, Д2, Д3, Д4, Д5, Д6
DOGONS_PER_CYCLE = 7

# Коэффициент догона: ×1.5 от ОКРУГЛЁННОЙ предыдущей, ceil вверх
DOGON_MULTIPLIER = 1.5

# Ставка только на игрока — 1 ставка на прогноз
BETS_PER_PREDICTION = 1


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

def next_dogon_bet(current_bet):
    """
    Следующая ставка догона: ×1.5 от ТЕКУЩЕЙ (уже округлённой),
    округление ВВЕРХ до целого рубля.

    Цепочка от 50:
        50 → 75 → 113 → 170 → 255 → 383 → 575
    """
    return int(math.ceil(float(current_bet) * DOGON_MULTIPLIER))


def bet_for_dogon(base_bet, dogon_index):
    """
    Сумма ставки (только игрок) для конкретного догона.

    Считаем от округлённой ставки шаг за шагом:
        Д0: 50
        Д1: 75
        Д2: 113
        Д3: 170
        Д4: 255
        Д5: 383
        Д6: 575
    """
    bet = int(round(float(base_bet)))

    for _ in range(int(dogon_index)):
        bet = next_dogon_bet(bet)

    return bet * BETS_PER_PREDICTION


# =====================================================================
# СПИСАНИЕ СТАВКИ
# =====================================================================

def apply_dogon_bet(prediction, dogon_index):

    base_bet = prediction.get("base_bet", bank_state["current_bet"])

    amount = bet_for_dogon(base_bet, dogon_index)

    # записываем фактическую ставку в прогноз (для сообщений)
    prediction["bet"] = amount

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
        {"card": "Q♣️", "where": "player"}
    """

    base_bet = prediction.get("base_bet", bank_state["current_bet"])

    one_bet = bet_for_dogon(base_bet, dogon_index)

    rank = prediction.get("predicted_rank")

    cf = get_player_cf(rank)

    payout = round(one_bet * cf, 2)

    bank_state["balance"] = round(bank_state["balance"] + payout, 2)

    # после победы база возвращается к стартовой
    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "rank": rank,
        "dogon": dogon_index,
        "card": found.get("card"),
        "where": "player",
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
    """
    Прогноз не зашёл на догоне dogon_index.

    Если это НЕ последний догон — списываем следующую ставку
    и записываем её в prediction["bet"] (для редактирования сообщения).
    Если последний — сбрасываем базу на START_BET.
    """

    if dogon_index < DOGONS_PER_CYCLE - 1:

        next_index = dogon_index + 1

        # спишет следующую ставку и обновит prediction["bet"]
        apply_dogon_bet(prediction, next_index)

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "rank": prediction.get("predicted_rank"),
            "dogon": dogon_index,
            "next_dogon": next_index,
            "next_bet": prediction.get("bet"),
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }

    else:

        # все 7 догонов проиграны — база сбрасывается
        bank_state["current_bet"] = START_BET

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "rank": prediction.get("predicted_rank"),
            "dogon": dogon_index,
            "cycle_lost": True,
            "next_base": START_BET,
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