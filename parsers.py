import re
from datetime import datetime
from config import (
    CARD_VALUES, SUITS, SUIT_ALIASES, MOSCOW_TZ,
    GAME_CYCLE
)

# =====================================================================
# КОНСТАНТЫ ДЛЯ НОВОГО АЛГОРИТМА
# =====================================================================
NEW_CYCLE = 1440  # Цикл игр (1440)
TRIGGER_RANKS = {"J", "Q", "K", "A"}  # Старшие карты (первая карта игрока)
RED_SUITS = {"♥️", "♦️"}
BLACK_SUITS = {"♠️", "♣️"}

CARD_RE = re.compile(r"(10|[2-9AJQKA])(♠|♣|♦|♥)\ufe0f?")

# =====================================================================
# БАЗОВЫЕ ФУНКЦИИ
# =====================================================================
def normalize_suit(suit):
    if suit is None: return None
    value = str(suit).strip()
    if value in SUIT_ALIASES: return SUIT_ALIASES[value]
    value = value.replace("\ufe0f", "")
    return SUITS.get(value)

def normalize_rank(rank):
    if rank is None: return None
    rank = str(rank).strip().upper()
    if rank == "А": rank = "A"
    if rank in {"2","3","4","5","6","7","8","9","10","J","Q","K","A"}: return rank
    return None

def card_to_text(card):
    if not card: return ""
    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))
    if not rank or not suit: return ""
    return f"{rank}{suit}"

def cards_to_text(cards):
    return " ".join(card_to_text(c) for c in cards if card_to_text(c))

def parse_cards(text):
    result = []
    if not text: return result
    for match in CARD_RE.finditer(text):
        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))
        if rank and suit:
            result.append({"rank": rank, "suit": suit})
    return result

def parse_game_message(text):
    if not text: return None
    number_match = re.search(r"#N(\d+)", text)
    if not number_match: return None
    game_number = int(number_match.group(1))
    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2: return None
    
    player_cards = parse_cards(groups[0])
    dealer_cards = parse_cards(groups[1])
    if not player_cards: return None

    player_score = sum(CARD_VALUES.get(normalize_rank(c["rank"]), 0) for c in player_cards)
    dealer_score = sum(CARD_VALUES.get(normalize_rank(c["rank"]), 0) for c in dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    is_draw = bool(re.search(r"#X\b", text))
    is_ochko = bool(re.search(r"#O\b", text))
    is_finished = bool(re.search(r"✅", text))
    is_r = bool(re.search(r"#R\b", text))

    return {
        "game_number": game_number, "game_id": game_id,
        "player_cards": player_cards, "dealer_cards": dealer_cards,
        "player_score": player_score, "dealer_score": dealer_score,
        "is_draw": is_draw, "is_ochko": is_ochko, "is_finished": is_finished, "is_r": is_r,
        "raw_text": text, "received_at": datetime.now(MOSCOW_TZ).isoformat(),
    }

# =====================================================================
# НОВЫЙ АЛГОРИТМ: ПОИСК ТРИГГЕРА
# =====================================================================
def find_new_trigger(game):
    player = game.get("player_cards", [])
    if not player: return None

    # 1. Первая карта — старшая (J, Q, K, A)
    first_card = player[0]
    first_rank = normalize_rank(first_card.get("rank"))
    if first_rank not in TRIGGER_RANKS: return None

    # 2. Нет 10 у игрока
    if any(normalize_rank(c.get("rank")) == "10" for c in player): return None

    # 3. Последняя карта — цифра (2-9)
    last_card = player[-1]
    last_rank = normalize_rank(last_card.get("rank"))
    if last_rank not in {"2","3","4","5","6","7","8","9"}: return None

    # 4. Масть первой и последней карты — РАЗНЫЕ
    first_suit = normalize_suit(first_card.get("suit"))
    last_suit = normalize_suit(last_card.get("suit"))
    if first_suit == last_suit: return None

    # 5. Нет дублей у игрока (проверяем совпадение РАНГОВ)
    ranks = [normalize_rank(c.get("rank")) for c in player]
    if len(ranks) != len(set(ranks)): return None

    return {
        "game_number": game.get("game_number"),
        "first_card_rank": first_rank,
        "first_card_suit": first_suit,
        "last_card_value": int(last_rank),
        "trigger_card_text": card_to_text(first_card),
    }

# =====================================================================
# РАСЧЕТ ПРОГНОЗА (ЦИКЛИЧНОСТЬ)
# =====================================================================
def calculate_prediction(trigger_data):
    game_num = trigger_data["game_number"]
    last_val = trigger_data["last_card_value"]
    first_rank = trigger_data["first_card_rank"]
    first_suit = trigger_data["first_card_suit"]

    # 1. Вычисляем целевую игру с учетом цикла 1440
    target_num = game_num + last_val
    if target_num > NEW_CYCLE:
        target_num = target_num - NEW_CYCLE
        if target_num > NEW_CYCLE: 
            target_num = target_num % NEW_CYCLE
    
    # 2. Формируем список целевых игр (4 игры)
    target_games = []
    for i in range(4):
        g_num = target_num + i
        if g_num > NEW_CYCLE:
            g_num = g_num - NEW_CYCLE
        target_games.append(g_num)

    # 3. Определяем масти прогноза (того же цвета, что и первая карта)
    if first_suit in BLACK_SUITS:
        predicted_suits = ["♠️", "♣️"]  # Черные (Пики, Крести)
    else:
        predicted_suits = ["♦️", "♥️"]  # Красные (Бубны, Черви)

    return {
        "predicted_rank": first_rank,
        "predicted_suits": predicted_suits,
        "target_games": target_games,
        "trigger_info": trigger_data
    }

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА (зашел ли прогноз)
# =====================================================================
def check_prediction_result(game, prediction):
    target_rank = prediction["predicted_rank"]
    target_suits = prediction["predicted_suits"]

    all_cards = game.get("player_cards", []) + game.get("dealer_cards", [])
    
    for card in all_cards:
        if not isinstance(card, dict):
            continue
            
        r = normalize_rank(card.get("rank"))
        s = normalize_suit(card.get("suit"))
        if r == target_rank and s in target_suits:
            return card_to_text(card)
    
    return None

# =====================================================================
# ЛОГ ИГРЫ (ДЛЯ КОНСОЛИ)
# =====================================================================
def log_game(game):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    print("", flush=True)
    print("────────────────────────────────────", flush=True)
    print(f"🎮 ИГРА #N{game['game_number']}", flush=True)
    print(
        f"👤 P: {game['player_score']} "
        f"({cards_to_text(player)})",
        flush=True,
    )
    print(
        f"🎰 D: {game['dealer_score']} "
        f"({cards_to_text(dealer)})",
        flush=True,
    )

    if game.get("is_draw"):
        print("🔰 #X — НИЧЬЯ", flush=True)

    if game.get("is_ochko"):
        print("⭕ #O — ОЧКО", flush=True)

    if game.get("is_r"):
        print("⚠️ #R — АНОМАЛИЯ", flush=True)

    if game.get("is_finished"):
        print("✅ — ИГРА ЗАВЕРШЕНА", flush=True)

    print("────────────────────────────────────", flush=True)