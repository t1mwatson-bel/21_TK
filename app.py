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
            
        # Поддержка старого и нового формата
        if isinstance(data, list):
            real_games = data
            last_num = real_games[0]["num"] if real_games else 0
            updated_at = time.time()
        else:
            real_games = data.get("games", [])
            last_num = data.get("last_game_num", 0)
            updated_at = data.get("updated_at", time.time())

        # Сортируем реальные игры по возрастанию (чтобы 1410 была выше 1411)
        real_games.sort(key=lambda x: x["num"])

        # 1. Берем последнюю реальную игру (активную)
        active_game = None
        for g in reversed(real_games):
            if not g["is_finished"]:
                active_game = g
                break
        
        # Если активной нет (все завершены), берем самую последнюю
        if not active_game and real_games:
            active_game = real_games[-1]

        result_games = []
        now = time.time()
        base_num = last_num

        # 2. Добавляем активную игру (если есть)
        if active_game:
            active_game["is_virtual"] = False
            active_game["time_left"] = 0
            active_game["status"] = "Событие идет" if not active_game["is_finished"] else "Игра завершена"
            result_games.append(active_game)
            base_num = max(base_num, active_game["num"])

        # 3. Генерируем будущие игры (виртуальные) по возрастанию
        for i in range(1, 5):
            future_num = base_num + i
            # Вычисляем таймер (каждую минуту новая игра)
            # Имитируем, что игра начнется через 60 секунд от последнего обновления
            elapsed = now - updated_at
            # Базовый таймер для первой виртуальной игры
            base_time_left = 60 - (elapsed % 60) 
            
            # Для каждой следующей прибавляем по 60 секунд
            time_left = int(base_time_left + (i - 1) * 60)
            
            result_games.append({
                "id": f"virtual_{future_num}",
                "num": future_num,
                "p_cards": [],
                "d_cards": [],
                "p_score": 0,
                "d_score": 0,
                "is_finished": False,
                "is_virtual": True,
                "time_left": time_left,
                "status": "Событие идет"
            })

        return jsonify(result_games)
    except Exception as e:
        print(f"Ошибка API games: {e}")
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