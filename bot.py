import os
import time
import json
import requests
from datetime import datetime
import pytz

# =====================================================================
# НАСТРОЙКИ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-1679.pro"
GAME_NUMBERS_FILE = "game_numbers.json"
LIVE_GAMES_FILE = "live_games.json"

game_numbers = {}
processed_games = set()
active_games_cache = {}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/2050671-baccara",
    "Cookie": "platform_type=desktop; auid=ua+l6mqq/My6Dm18AzGiAg==; lng=ru; cookies_agree_type=3; tzo=5; is12h=0; fatman_uuid=6130f537-4410-1609-a97d-d8e42c9bd207; che_g=256be28c-6643-4128-98f0-0b48cd8335d1; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; reflinkid=s_50970m_355c_; sh.session.id=8fd49537-b74b-421a-937d-c6cd70d37f4f; SESSION=5b56b60163a07a81e5a746330aceeb18; _ga=GA1.1.331026452.1789590750; _ga_7JGWL9SV66=GS2.1.s1789590750$o1$g1$t1789590772$j38$l0$h724394072; window_width=1056"
}

# =====================================================================
# ФУНКЦИИ
# =====================================================================

def load_game_numbers():
    global game_numbers
    try:
        if os.path.exists(GAME_NUMBERS_FILE):
            with open(GAME_NUMBERS_FILE, "r", encoding="utf-8") as f:
                game_numbers = json.load(f)
    except Exception:
        game_numbers = {}

def get_current_game_number():
    dt = datetime.now(MOSCOW_TZ)
    minutes = (dt.hour * 60 + dt.minute - 180) % 1440
    return minutes + 1

def get_game_number(game_id):
    game_id = str(game_id)
    if game_id in game_numbers:
        return int(game_numbers[game_id])
    number = get_current_game_number()
    game_numbers[game_id] = number
    with open(GAME_NUMBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(game_numbers, f)
    return number

def parse_cards(value_str):
    if not value_str or value_str == "[]": return []
    try:
        return json.loads(value_str) if isinstance(value_str, str) else value_str
    except Exception:
        return []

def format_cards(cards):
    res = []
    for c in cards:
        suit = SUITS_NAMES.get(c.get("S", 0), "?")
        rank = RANKS.get(c.get("R", 0), str(c.get("R", 0)))
        res.append(f"{rank}{suit}")
    return res

def calculate_score(cards):
    score = 0
    for c in cards:
        r = c.get("R", 0)
        if r in (1, 14): score += 1
        elif 2 <= r <= 9: score += r
    return score % 10

def get_active_games():
    try:
        url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=1&gr=2336&grMode=4&lng=ru&ref=1&selectedMs=1.236.2050671,10.236"
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()
            games = data if isinstance(data, list) else data.get("Value", [])
            return [g for g in games if g.get("liga", {}).get("id") == 2050671]
    except Exception as e:
        pass
    return []

def update_live_json():
    games = get_active_games()
    if not games: return

    result = []
    
    for game in games:
        game_id = str(game.get("id"))
        main_stat = game.get("scores", {}).get("statistic", {}).get("main", {})
        p_cards_raw = parse_cards(main_stat.get("P", "[]"))
        d_cards_raw = parse_cards(main_stat.get("B", "[]"))
        
        if not p_cards_raw and not d_cards_raw:
            continue

        game_num = get_game_number(game_id)
        p_score = calculate_score(p_cards_raw)
        d_score = calculate_score(d_cards_raw)
        
        state = str(main_stat.get("S", "")).lower()
        is_finished = state in ("win1", "win2", "draw", "finished", "gameover", "endgame", "result", "resultgame", "completed")
        
        game_data = {
            "id": game_id,
            "num": game_num,
            "p_cards": format_cards(p_cards_raw),
            "d_cards": format_cards(d_cards_raw),
            "p_score": p_score,
            "d_score": d_score,
            "is_finished": is_finished,
            "winner": "player" if state == "win1" else ("banker" if state == "win2" else ("draw" if state == "draw" else ""))
        }
        
        result.append(game_data)
    
    result.sort(key=lambda x: x["num"], reverse=True)
    
    with open(LIVE_GAMES_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

# =====================================================================
# ГЛАВНАЯ ФУНКЦИЯ ДЛЯ ЗАПУСКА ИЗ APP.PY
# =====================================================================
def main():
    load_game_numbers()
    print("🔄 СБОРЩИК ДАННЫХ ДЛЯ САЙТА ЗАПУЩЕН", flush=True)
    while True:
        try:
            update_live_json()
            time.sleep(2)
        except Exception as e:
            print(f"❌ Ошибка в сборщике: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()