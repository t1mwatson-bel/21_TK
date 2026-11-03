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
# КОНСТАНТЫ ЛОГИКИ СТАВОК
# =====================================================================

# 4 догона: Д0, Д1, Д2, Д3 (под новую логику)
DOGONS_PER_CYCLE = 4

# Коэффициент догона: ×1.5
DOGON_MULTIPLIER = 1.5

# Ставка только на игрока
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
    """Следующая ставка догона: ×1.5, округление вверх."""
    return int(math.ceil(float(current_bet) * DOGON_MULTIPLIER))


def bet_for_dogon(base_bet, dogon_index):
    """Сумма ставки для конкретного догона."""
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

    # Записываем фактическую ставку в прогноз
    prediction["bet"] = amount

    bank_state["balance"] = round(bank_state["balance"] - amount, 2)

    # Берем номер игры из target_games (первую игру пула)
    target_games = prediction.get("target_games", [])
    game_number = target_games[0] if target_games else None

    record = {
        "type": "bet",
        "game_number": game_number,
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
    found — СТРОКА (например, "J♣️").
    """
    base_bet = prediction.get("base_bet", bank_state["current_bet"])
    one_bet = bet_for_dogon(base_bet, dogon_index)
    rank = prediction.get("predicted_rank")
    cf = get_player_cf(rank)
    payout = round(one_bet * cf, 2)

    bank_state["balance"] = round(bank_state["balance"] + payout, 2)
    bank_state["current_bet"] = START_BET

    target_games = prediction.get("target_games", [])
    game_number = target_games[0] if target_games else None

    record = {
        "type": "win",
        "game_number": game_number,
        "rank": rank,
        "dogon": dogon_index,
        "card": found,  # Теперь это строка
        "where": "player_or_dealer",  # Мы ищем у обоих
        "cf": cf,
        "bet": one_bet,
        "payout": payout,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: {found} × {cf} → {payout} ₽ | "
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
    ВАЖНО: НЕ списывает следующую ставку (это делает bot.py через apply_dogon_bet).
    """
    target_games = prediction.get("target_games", [])
    game_number = target_games[0] if target_games else None

    if dogon_index < DOGONS_PER_CYCLE - 1:
        record = {
            "type": "lose",
            "game_number": game_number,
            "rank": prediction.get("predicted_rank"),
            "dogon": dogon_index,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }
    else:
        # Все догоны проиграны — база сбрасывается
        bank_state["current_bet"] = START_BET
        record = {
            "type": "lose",
            "game_number": game_number,
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

    target_games = prediction.get("target_games", [])
    game_number = target_games[0] if target_games else None

    record = {
        "type": "return",
        "game_number": game_number,
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