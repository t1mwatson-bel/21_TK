# -*- coding: utf-8 -*-

from datetime import datetime

from config import (
    MOSCOW_TZ,
)

from bank import get_bank_summary


# ============================================================
# ОБЩАЯ СТАТИСТИКА
# ============================================================

def get_predictions_summary(predictions):

    total = 0
    wins = 0
    loses = 0

    for p in predictions:

        status = p.get("status")

        if status == "win":
            total += 1
            wins += 1

        elif status == "lose":
            total += 1
            loses += 1

    winrate = (
        round(wins / total * 100, 1)
        if total else 0.0
    )

    bank = get_bank_summary()

    return {
        "total": total,
        "wins": wins,
        "loses": loses,
        "winrate": winrate,
        "profit": bank["profit"],
        "roi": bank["roi"],
        "balance": bank["balance"],
        "start_balance": bank["start_balance"],
        "current_bet": bank["current_bet"],
    }


# ============================================================
# СТАТИСТИКА ПО ДОГОНАМ
# ============================================================

def get_dogon_stats(predictions):

    stats = {
        0: {"played": 0, "wins": 0},
        1: {"played": 0, "wins": 0},
        2: {"played": 0, "wins": 0},
        3: {"played": 0, "wins": 0},
        4: {"played": 0, "wins": 0},
    }

    for p in predictions:

        status = p.get("status")

        if status == "win":

            try:
                dogon = int(p.get("dogon", 0))
            except (TypeError, ValueError):
                continue

            if dogon not in stats:
                continue

            for d in range(dogon + 1):
                stats[d]["played"] += 1

            stats[dogon]["wins"] += 1

        elif status == "lose":

            for d in stats:
                stats[d]["played"] += 1

    result = []

    for dogon in sorted(stats):

        played = stats[dogon]["played"]
        wins = stats[dogon]["wins"]

        winrate = (
            round(wins / played * 100, 1)
            if played else 0.0
        )

        result.append({
            "dogon": f"Д{dogon}",
            "played": played,
            "wins": wins,
            "winrate": winrate,
        })

    return result


# ============================================================
# СТАТИСТИКА ПО МАСТЯМ
# ============================================================

def get_suit_stats(predictions):

    stats = {
        "♠️": {"total": 0, "wins": 0},
        "♥️": {"total": 0, "wins": 0},
        "♦️": {"total": 0, "wins": 0},
        "♣️": {"total": 0, "wins": 0},
    }

    for p in predictions:

        status = p.get("status")
        suit = p.get("predicted_suit")

        if suit not in stats:
            continue

        if status in ("win", "lose"):

            stats[suit]["total"] += 1

            if status == "win":
                stats[suit]["wins"] += 1

    result = []

    for suit in ["♦️", "♣️", "♠️", "♥️"]:

        total = stats[suit]["total"]
        wins = stats[suit]["wins"]

        winrate = (
            round(wins / total * 100, 1)
            if total else 0.0
        )

        result.append({
            "suit": suit,
            "total": total,
            "wins": wins,
            "winrate": winrate,
        })

    return result


# ============================================================
# СТАТИСТИКА ПО ДНЯМ
# ============================================================

def get_daily_stats(predictions, limit_days=30):

    by_date = {}

    for p in predictions:

        status = p.get("status")

        if status not in ("win", "lose"):
            continue

        created = p.get("created_at")

        if not created:
            continue

        try:
            dt = datetime.fromisoformat(created)
            day = dt.strftime("%Y-%m-%d")
        except Exception:
            continue

        if day not in by_date:

            by_date[day] = {
                "total": 0,
                "wins": 0,
                "loses": 0,
            }

        by_date[day]["total"] += 1

        if status == "win":
            by_date[day]["wins"] += 1
        else:
            by_date[day]["loses"] += 1

    days = sorted(
        by_date.keys(),
        reverse=True
    )[:limit_days]

    result = []

    for day in days:

        data = by_date[day]
        total = data["total"]
        wins = data["wins"]

        winrate = (
            round(wins / total * 100, 1)
            if total else 0.0
        )

        result.append({
            "date": day,
            "total": total,
            "wins": wins,
            "loses": data["loses"],
            "winrate": winrate,
        })

    return result


# ============================================================
# ПОСЛЕДНИЕ ПРОГНОЗЫ — ТОЛЬКО ЗАКРЫТЫЕ
# ============================================================

def get_last_predictions(predictions, limit=30):

    closed = [
        p for p in predictions
        if p.get("status") in ("win", "lose")
    ]

    result = []

    for p in closed[-limit:][::-1]:

        result.append({
            "target": p.get("target_number"),
            "trigger": p.get("trigger_number"),
            "suit": p.get("predicted_suit"),
            "status": p.get("status"),
            "dogon": p.get("dogon"),
            "bet": p.get("bet"),
            "created_at": p.get("created_at"),
            "closed_at": p.get("closed_at"),
        })

    return result


# ============================================================
# ПРИРОСТ БАЛАНСА ПО ДНЯМ
# ============================================================

def get_profit_daily(predictions, coefficient=1.9):
    """
    Возвращает накопительный баланс по дням на основе
    закрытых прогнозов (win / lose).

    win  -> + bet * (coefficient - 1)
    lose -> - bet
    """

    daily_delta = {}

    for p in predictions:

        status = p.get("status")

        if status not in ("win", "lose"):
            continue

        closed = p.get("closed_at") or p.get("created_at")

        if not closed:
            continue

        try:
            dt = datetime.fromisoformat(closed)
            day = dt.strftime("%Y-%m-%d")
        except Exception:
            continue

        bet = p.get("bet", 0) or 0

        try:
            bet = float(bet)
        except (TypeError, ValueError):
            bet = 0.0

        if status == "win":
            delta = bet * (coefficient - 1)
        else:
            delta = -bet

        daily_delta[day] = daily_delta.get(day, 0.0) + delta

    result = []
    total = 0.0

    for day in sorted(daily_delta.keys()):

        total += daily_delta[day]

        result.append({
            "date": day,
            "delta": round(daily_delta[day], 2),
            "total": round(total, 2),
        })

    return result


# ============================================================
# ПОЛНАЯ СТАТИСТИКА ДЛЯ САЙТА
# ============================================================

def get_full_stats(predictions):

    return {
        "summary": get_predictions_summary(predictions),
        "dogons": get_dogon_stats(predictions),
        "suits": get_suit_stats(predictions),
        "daily": get_daily_stats(predictions),
        "profit_daily": get_profit_daily(predictions),
        "last": get_last_predictions(predictions, 30),
        "updated_at": datetime.now(MOSCOW_TZ).isoformat(),
    }
