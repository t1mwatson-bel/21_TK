import os
import json

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    BET_MULTIPLIER,
    COEFFICIENT,
    DOGON_GAMES,
    MOSCOW_TZ,
)


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
# СТАВКИ ДОГОНОВ
# =====================================================================

def bet_for_dogon(base_bet, dogon_index):
    """Ставка на догоне: base_bet × 2.5^dogon."""
    return round(base_bet * (BET_MULTIPLIER ** dogon_index), 2)


# =====================================================================
# РЕЗУЛЬТАТЫ ПРОГНОЗА
# =====================================================================

def apply_win(prediction, dogon_index, bet_amount):
    """
    WIN на догоне `dogon_index`.
    bet_amount = ставка на ЭТОМ догоне (= base_bet × 2.5^dogon).
    Проигранные до него догоны списываем из баланса.
    """
    base_bet = prediction.get("base_bet", get_current_bet())
    payout = round(bet_amount * COEFFICIENT, 2)

    # Проигранные догоны Д0..Д(dogon_index-1) — считаем от base_bet
    total_lost = sum(
        bet_for_dogon(base_bet, i) for i in range(dogon_index)
    )

    # Профит = выплата - ставка победившего догона - все проигранные до него
    profit = round(payout - bet_amount - total_lost, 2)

    bank_state["balance"] = round(
        bank_state["balance"] + payout - bet_amount - total_lost, 2
    )
    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "suit": prediction.get("predicted_suit"),
        "dogon": dogon_index,
        "bet": bet_amount,
        "total_lost_before": total_lost,
        "payout": payout,
        "profit": profit,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: ставка {bet_amount} ₽ → выплата {payout} ₽ "
        f"(профит {profit} ₽) | баланс {bank_state['balance']} ₽",
        flush=True,
    )
    return record


def apply_lose(prediction, bet_amount):
    """
    LOSE (все Д0..Д3 проиграны).
    bet_amount = base_bet (ставка Д0).
    Списываем СУММУ всех догонов, следующая базовая ставка = base × 2.5^4.
    """
    base_bet = bet_amount
    total_lost = sum(
        bet_for_dogon(base_bet, i) for i in range(DOGON_GAMES + 1)
    )

    bank_state["balance"] = round(bank_state["balance"] - total_lost, 2)
    new_bet = round(base_bet * (BET_MULTIPLIER ** (DOGON_GAMES + 1)), 2)
    bank_state["current_bet"] = new_bet

    record = {
        "type": "lose",
        "game_number": prediction.get("target_number"),
        "suit": prediction.get("predicted_suit"),
        "bet": base_bet,
        "total_lost": total_lost,
        "next_bet": new_bet,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"❌ LOSE: потеряно {total_lost} ₽ | "
        f"следующая ставка {new_bet} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )
    return record


def apply_return(prediction, bet_amount):
    """
    RETURN ♻️ (таймаут).
    Баланс не меняется, current_bet сбрасывается на START_BET.
    """
    bank_state["current_bet"] = START_BET

    record = {
        "type": "return",
        "game_number": prediction.get("target_number"),
        "suit": prediction.get("predicted_suit"),
        "bet": bet_amount,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"♻️ ВОЗВРАТ: ставка сброшена на {START_BET} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )
    return record


# =====================================================================
# СТАТИСТИКА ДЛЯ САЙТА
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
