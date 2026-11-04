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

from coefs import get_player_cf, get_dealer_cf


# =====================================================================
# КОНСТАНТЫ ЛОГИКИ СТАВОК (ЭХО)
# =====================================================================

DOGONS_PER_CYCLE = 4
DOGON_BETS = (50, 75, 100, 125)  # Фиксированные ставки
BETS_PER_PREDICTION = 2  # 2 ставки (на 2 масти)


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
# СПИСАНИЕ СТАВОК (2 ШТУКИ)
# =====================================================================

def apply_dogon_bet(prediction, dogon_index):
    # Берем фиксированную ставку из DOGON_BETS (50, 75, 100, 125)
    one_bet = DOGON_BETS[dogon_index] if dogon_index < len(DOGON_BETS) else 125
    
    # В Эхо 2 ставки (на 2 масти для одного человека)
    total_bet = one_bet * 2
    prediction["bet"] = total_bet

    bank_state["balance"] = round(bank_state["balance"] - total_bet, 2)

    target_games = prediction.get("target_games", [])
    game_number = target_games[dogon_index] if dogon_index < len(target_games) else None
    rank = prediction.get("predicted_rank")
    suits = prediction.get("predicted_suits", ["♠️", "♣️"])
    target_who = prediction.get("target_who", "both")

    for suit in suits:
        record = {
            "type": "bet",
            "game_number": game_number,
            "rank": rank,
            "suit": suit,
            "who": target_who,
            "dogon": dogon_index,
            "amount": one_bet,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }
        bank_state["history"].append(record)

    save_bank()
    print(f"💸 BET Д{dogon_index}: списано {total_bet} ₽ (2×{one_bet}) | баланс {bank_state['balance']} ₽", flush=True)
    return True


# =====================================================================
# РЕЗУЛЬТАТ: WIN
# =====================================================================

def apply_win(prediction, dogon_index, found_cards_data):
    one_bet = DOGON_BETS[dogon_index] if dogon_index < len(DOGON_BETS) else 125
    total_payout = 0

    for card_data in found_cards_data:
        rank = card_data["rank"]
        suit = card_data["suit"]
        who = card_data["who"]

        if who == "player":
            cf = get_player_cf(rank, suit)
        else:
            cf = get_dealer_cf(rank, suit)

        payout = round(one_bet * cf, 2)
        total_payout += payout
        
        target_games = prediction.get("target_games", [])
        game_number = target_games[dogon_index] if dogon_index < len(target_games) else None
        
        record = {
            "type": "win",
            "game_number": game_number,
            "rank": rank,
            "suit": suit,
            "who": who,
            "card": card_data["card"],
            "dogon": dogon_index,
            "cf": cf,
            "bet": one_bet,
            "payout": payout,
            "balance_after": bank_state["balance"] + total_payout,
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }
        bank_state["history"].append(record)

    bank_state["balance"] = round(bank_state["balance"] + total_payout, 2)
    bank_state["current_bet"] = START_BET

    save_bank()
    print(f"💰 WIN: {total_payout} ₽ | баланс {bank_state['balance']} ₽", flush=True)
    return True


# =====================================================================
# РЕЗУЛЬТАТ: LOSE
# =====================================================================

def apply_lose(prediction, dogon_index):
    """
    Прогноз не зашёл на догоне dogon_index.
    ВАЖНО: НЕ списывает следующую ставку (это делает bot.py через apply_dogon_bet).
    """
    target_games = prediction.get("target_games", [])
    game_number = target_games[dogon_index] if dogon_index < len(target_games) else None
    target_who = prediction.get("target_who", "both")

    if dogon_index < DOGONS_PER_CYCLE - 1:
        record = {
            "type": "lose",
            "game_number": game_number,
            "rank": prediction.get("predicted_rank"),
            "who": target_who,
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
            "who": target_who,
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