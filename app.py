import json
import os
import threading
import time
from flask import Flask, render_template, jsonify, request
import bot

app = Flask(__name__)

LIVE_GAMES_FILE = "live_games.json"
USER_FILE = "user_demo.json"

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
            data = json.load(f)
            
        real_games = data.get("games", [])
        last_num = data.get("last_game_num", 0)
        updated_at = data.get("updated_at", time.time())

        virtual_games = []
        now = time.time()
        base_num = last_num if last_num > 0 else 1

        # Генерируем 4 будущие игры
        for i in range(1, 5):
            future_num = base_num + i
            time_left = int(60 - (now - updated_at) + (i - 1) * 60)
            if time_left < 0: time_left = 0
            
            virtual_games.append({
                "id": f"virtual_{future_num}",
                "num": future_num,
                "p_cards": [],
                "d_cards": [],
                "p_score": 0,
                "d_score": 0,
                "is_finished": False,
                "is_virtual": True,
                "time_left": time_left,
                "status": "Событие идет" if time_left > 0 else "Ожидание"
            })

        # Берем 3 последних реальных игры
        for g in real_games[:3]:
            g["is_virtual"] = False
            g["time_left"] = 0
            g["status"] = "Игра завершена" if g["is_finished"] else "Событие идет"

        all_games = real_games[:3] + virtual_games
        all_games.sort(key=lambda x: x["num"], reverse=True)
        
        return jsonify(all_games)
    except Exception as e:
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
    amount = float(data.get("amount", 0))

    with open(USER_FILE, "r", encoding="utf-8") as f:
        user = json.load(f)

    if user["balance"] < amount:
        return jsonify({"status": "error", "message": "Недостаточно средств"})

    user["balance"] -= amount
    user["bets"].append({
        "game_id": game_id,
        "amount": amount,
        "status": "pending"
    })

    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump(user, f, ensure_ascii=False)

    return jsonify({"status": "ok", "balance": user["balance"]})

def run_fetcher():
    time.sleep(2) 
    try:
        bot.main() 
    except Exception as e:
        import traceback
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА В БОТЕ: {e}", flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_fetcher, daemon=True).start()
    app.run(host='0.0.0.0', port=3000, debug=False)