import os
import json
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)

PREDICTIONS_FILE = os.getenv("PREDICTIONS_FILE", "predictions.json")
PORT = int(os.getenv("PORT", "8080"))

try:
    from bank import get_bank_summary, get_bank_history
    BANK_AVAILABLE = True
except Exception as e:
    print(f"⚠️ Не удалось импортировать bank.py: {e}")
    BANK_AVAILABLE = False


# ============================================================
# ЗАГРУЗКА ПРОГНОЗОВ
# ============================================================

def load_predictions():
    if not os.path.exists(PREDICTIONS_FILE):
        return []

    try:
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            if isinstance(data.get("predictions"), list):
                return data["predictions"]
            return []

        return []

    except Exception as e:
        print(f"Ошибка чтения {PREDICTIONS_FILE}: {e}")
        return []


# ============================================================
# РЕЗУЛЬТАТ
# ============================================================

def get_result(prediction):

    result = prediction.get("result")

    if result is not None:
        result = str(result).lower().strip()

        if result in ("hit", "win", "won", "success", "зашло", "yes"):
            return "hit"

        if result in ("miss", "lose", "lost", "не зашло", "no"):
            return "miss"

        if result in ("pending", "wait", "waiting"):
            return "pending"

    status = str(prediction.get("status", "")).lower().strip()

    if status in ("hit", "win", "won"):
        return "hit"

    if status in ("miss", "lose", "lost"):
        return "miss"

    # pending / active / waiting / pending_check / cancelled → pending
    if status in ("pending", "active", "waiting", "pending_check", "cancelled"):
        return "pending"

    return "pending"


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ
# ============================================================

def get_dogon(prediction):
    try:
        return int(prediction.get("dogon", 0))
    except Exception:
        return 0


def get_rank(prediction):
    """Возвращает ранг прогноза (6-10, J, Q, K, A)."""
    rank = prediction.get("predicted_rank")
    if rank:
        return str(rank)
    return "—"


def get_target_offset(prediction):
    try:
        return int(prediction.get("target_offset", 0))
    except Exception:
        return 0


def get_cf(prediction):
    """Возвращает коэффициент (только игрок — 21)."""

    cf_player = prediction.get("cf_player")

    if cf_player:
        return {"player": float(cf_player)}

    return None


def calc_stats(predictions):

    completed = []

    for p in predictions:
        result = get_result(p)
        if result in ("hit", "miss"):
            completed.append((p, result))

    total = len(completed)
    hits = sum(1 for _, r in completed if r == "hit")
    misses = sum(1 for _, r in completed if r == "miss")

    accuracy = (hits / total * 100) if total else 0

    return {
        "total": total,
        "hits": hits,
        "misses": misses,
        "accuracy": round(accuracy, 2),
    }


def group_stats(predictions, key_function):

    groups = {}

    for prediction in predictions:
        result = get_result(prediction)
        if result not in ("hit", "miss"):
            continue

        key = key_function(prediction)

        if key not in groups:
            groups[key] = {"total": 0, "hits": 0, "misses": 0}

        groups[key]["total"] += 1
        if result == "hit":
            groups[key]["hits"] += 1
        else:
            groups[key]["misses"] += 1

    for value in groups.values():
        total = value["total"]
        value["accuracy"] = round(value["hits"] / total * 100, 2) if total else 0

    return groups


def calculate_streak(predictions):

    completed = []

    for p in predictions:
        result = get_result(p)
        if result in ("hit", "miss"):
            completed.append(result)

    if not completed:
        return {"type": "none", "count": 0}

    last = completed[-1]
    count = 0

    for result in reversed(completed):
        if result == last:
            count += 1
        else:
            break

    return {"type": last, "count": count}


def calculate_max_streak(predictions):

    max_hit = 0
    max_miss = 0
    current_hit = 0
    current_miss = 0

    for p in predictions:
        result = get_result(p)

        if result == "hit":
            current_hit += 1
            current_miss = 0
            max_hit = max(max_hit, current_hit)
        elif result == "miss":
            current_miss += 1
            current_hit = 0
            max_miss = max(max_miss, current_miss)

    return {"hit": max_hit, "miss": max_miss}


# ============================================================
# API БАНКА
# ============================================================

@app.route("/api/bank")
def api_bank():
    if not BANK_AVAILABLE:
        return jsonify({"error": "bank.py недоступен"}), 500
    try:
        return jsonify(get_bank_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bank/history")
def api_bank_history():
    if not BANK_AVAILABLE:
        return jsonify({"error": "bank.py недоступен"}), 500
    try:
        return jsonify(get_bank_history(limit=100))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# API СТАТИСТИКИ
# ============================================================

@app.route("/api/stats")
def api_stats():

    predictions = load_predictions()

    overall = calc_stats(predictions)

    # Догоны
    dogon_groups = group_stats(predictions, lambda p: get_dogon(p))

    dogon_stats = []
    for dogon in range(4):
        data = dogon_groups.get(dogon, {"total": 0, "hits": 0, "misses": 0, "accuracy": 0})
        dogon_stats.append({"dogon": dogon, **data})

    # Ранги
    rank_groups = group_stats(predictions, lambda p: get_rank(p))

    ranks = []
    for rank, data in rank_groups.items():
        ranks.append({"rank": rank, **data})

    ranks.sort(key=lambda x: x["accuracy"], reverse=True)

    # Rolling
    completed_predictions = [p for p in predictions if get_result(p) in ("hit", "miss")]

    def rolling_stats(amount):
        subset = completed_predictions[-amount:]
        total = len(subset)
        hits = sum(1 for p in subset if get_result(p) == "hit")
        accuracy = (hits / total * 100) if total else 0
        return {
            "total": total,
            "hits": hits,
            "misses": total - hits,
            "accuracy": round(accuracy, 2),
        }

    rolling = {
        "100": rolling_stats(100),
        "500": rolling_stats(500),
        "1000": rolling_stats(1000),
    }

    # График
    chart_predictions = []
    for p in completed_predictions[-100:]:
        chart_predictions.append({
            "game": p.get("target_number") or p.get("result_game") or p.get("trigger_number") or "—",
            "result": get_result(p),
            "rank": get_rank(p),
            "dogon": get_dogon(p),
        })

    # Последний прогноз
    latest = None
    if predictions:
        p = predictions[-1]
        latest = {
            "trigger_number": p.get("trigger_number", "—"),
            "target_number": p.get("target_number", "—"),
            "rank": get_rank(p),
            "dogon": get_dogon(p),
            "result": get_result(p),
            "target_offset": get_target_offset(p),
            "cf": get_cf(p),
        }

    return jsonify({
        "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "overall": overall,
        "streak": calculate_streak(predictions),
        "max_streak": calculate_max_streak(predictions),
        "dogon": dogon_stats,
        "ranks": ranks,
        "rolling": rolling,
        "chart": chart_predictions,
        "latest": latest,
    })


# ============================================================
# ГЛАВНАЯ
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ============================================================
# ЗАПУСК
# ============================================================

def start_web_server():

    print("=" * 60)
    print("21 — RANK STATISTICS")
    print("=" * 60)
    print(f"Predictions: {PREDICTIONS_FILE}")
    print(f"Port: {PORT}")

    def _run():
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    print(f"🌐 Web-server запущен на порту {PORT}", flush=True)
    return t


if __name__ == "__main__":
    start_web_server()
    while True:
        time.sleep(60)