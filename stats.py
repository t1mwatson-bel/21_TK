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
    returns = 0
    pending = 0

    for p in predictions:

        status = p.get("status")

        if status == "win":
            total += 1
            wins += 1

        elif status == "lose":
            total += 1
            loses += 1

        elif status in ("return", "void", "expired"):
            returns += 1

        elif status == "pending":
            pending += 1

    winrate = (
        round(wins / total * 100, 1)
        if total else 0.0
    )

    bank = get_bank_summary()

    return {
        "total": total,
        "wins": wins,
        "loses": loses,
        "returns": returns,
        "pending": pending,
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

            # Все попытки до выигрышного догона сыграны
            for d in range(dogon + 1):
                stats[d]["played"] += 1

            # Выигрыш засчитываем на конкретном догоне
            stats[dogon]["wins"] += 1

        elif status == "lose":

            # Проигранная серия прошла все четыре догона
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
# ПОСЛЕДНИЕ ПРОГНОЗЫ
# ============================================================

def get_last_predictions(predictions, limit=30):

    result = []

    for p in predictions[-limit:][::-1]:

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
# ПОЛНАЯ СТАТИСТИКА ДЛЯ САЙТА
# ============================================================

def get_full_stats(predictions):

    return {
        "summary": get_predictions_summary(predictions),
        "dogons": get_dogon_stats(predictions),
        "suits": get_suit_stats(predictions),
        "daily": get_daily_stats(predictions),
        "last": get_last_predictions(predictions, 30),
        "updated_at": datetime.now(MOSCOW_TZ).isoformat(),
    }