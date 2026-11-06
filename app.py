import json
import os
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

LIVE_GAMES_FILE = "live_games.json"
USER_FILE = "user_demo.json"

# Создаем демо-пользователя, если его нет
if not os.path.exists(USER_FILE):
    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump({"balance": 46000.0, "bets": []}, f)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/games')
def api_games():
    try:
        with open(LIVE_GAMES_FILE, "r", encoding="utf-8") as f:
            games = json.load(f)
        return jsonify(games)
    except Exception:
        return jsonify([])

@app.route('/api/user')
def api_user():
    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            user = json.load(f)
        return jsonify(user)
    except Exception:
        return jsonify({"balance": 0, "bets": []})

@app.route('/api/bet', methods=['POST'])
def api_bet():
    data = request.json
    game_id = data.get("game_id")
    suit = data.get("suit")
    amount = float(data.get("amount", 0))

    with open(USER_FILE, "r", encoding="utf-8") as f:
        user = json.load(f)

    if user["balance"] < amount:
        return jsonify({"status": "error", "message": "Недостаточно средств"})

    user["balance"] -= amount
    user["bets"].append({
        "game_id": game_id,
        "suit": suit,
        "amount": amount,
        "status": "pending"
    })

    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump(user, f, ensure_ascii=False)

    return jsonify({"status": "ok", "balance": user["balance"]})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)