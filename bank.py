import os
import json

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    BET_MULTIPLIER,
    MOSCOW_TZ,
)

from coefs import get_dealer_cf


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
    """Загружает состояние банка из файла."""

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
    """Сохраняет состояние банка в файл."""

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
# ЛОГИКА СЕРИИ
# =====================================================================

def bet_for_dogon(base_bet, dogon_index):
    """
    Считает ставку для конкретного догона.
    dogon_index: 0 = Д0, 1 = Д1, 2 = Д2, 3 = Д3.
    """

    return round(base_bet * (BET_MULTIPLIER ** dogon_index), 2)


# =====================================================================
# ПРИМЕНЕНИЕ РЕЗУЛЬТАТА ПРОГНОЗА
# =====================================================================

def apply_win(prediction, dogon_index, bet_amount, cf=None):
    """
    Прогноз выиграл.
    cf — коэффициент на карту (если не передан — берём из coefs.py по карте).
    Списываем все проигранные догоны до win, начисляем выплату,
    сбрасываем current_bet на START_BET.
    """

    if cf is None:
        cf = get_dealer_cf(prediction.get("predicted_card", ""))

    payout = round(bet_amount * cf, 2)

    # Если win был не на Д0 — все предыдущие догоны были проиграны
    total_lost = 0.0
    for i in range(dogon_index):
        total_lost += bet_for_dogon(get_current_bet(), i)

    # Профит = payout - ставка - все проигранные догоны до этого
    profit = round(payout - bet_amount - total_lost, 2)

    bank_state["balance"] = round(
        bank_state["balance"] + payout - bet_amount - total_lost,
        2,
    )
    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "suit": prediction.get("predicted_suit"),
        "predicted_card": prediction.get("predicted_card"),
        "dogon": dogon_index,
        "bet": bet_amount,
        "cf": cf,
        "payout": payout,
        "profit": profit,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: {prediction.get('predicted_card')} × {cf} | "
        f"ставка {bet_amount} ₽ → выплата {payout} ₽ "
        f"(профит {profit} ₽) | баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_lose(prediction, bet_amount):
    """
    Прогноз проиграл (все 4 игры).
    Умножаем current_bet на 2.5 для следующего прогноза.
    """

    total_lost = 0.0
    for i in range(4):  # Д0 + 3 догона
        total_lost += bet_for_dogon(bet_amount, i)

    bank_state["balance"] = round(bank_state["balance"] - total_lost, 2)

    new_bet = round(bet_amount * (BET_MULTIPLIER ** 4), 2)
    bank_state["current_bet"] = new_bet

    record = {
        "type": "lose",
        "game_number": prediction.get("target_number"),
        "suit": prediction.get("predicted_suit"),
        "bet": bet_amount,
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
    Возврат ♻️ (таймаут >30 мин).
    Ставка сбрасывается на START_BET, баланс не меняется.
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
# СТАТИСТИКА БАНКА (для сайта)
# =====================================================================

def get_bank_summary():
    """Возвращает сводку по банку для сайта."""

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
