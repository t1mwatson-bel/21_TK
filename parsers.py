import re

from datetime import datetime

from config import (
    CARD_VALUES,
    SUITS,
    SUIT_ALIASES,
    GAME_CYCLE,
    MOSCOW_TZ,
)


# =====================================================================
# РЕГУЛЯРКА ДЛЯ КАРТ
# =====================================================================

CARD_RE = re.compile(
    r"(10|[2-9AJQKA])"
    r"(♠|♣|♦|♥)"
    r"\ufe0f?"
)


# =====================================================================
# РАНГИ (только цифры 2-10 для триггера)
# =====================================================================

DIGIT_RANKS = {"2", "3", "4", "5", "6", "7", "8", "9", "10"}


# =====================================================================
# НОРМАЛИЗАЦИЯ
# =====================================================================

def normalize_suit(suit):
    if suit is None:
        return None

    value = str(suit).strip()

    if value in SUIT_ALIASES:
        return SUIT_ALIASES[value]

    value = value.replace("\ufe0f", "")

    return SUITS.get(value)


def normalize_rank(rank):
    if rank is None:
        return None

    rank = str(rank).strip().upper()

    if rank == "А":
        rank = "A"

    if rank in {
        "2", "3", "4", "5",
        "6", "7", "8", "9",
        "10", "J", "Q", "K", "A",
    }:
        return rank

    return None


# =====================================================================
# КАРТЫ → ТЕКСТ
# =====================================================================

def card_to_text(card):
    if not card:
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}"


def cards_to_text(cards):
    result = []

    for card in cards:
        value = card_to_text(card)

        if value:
            result.append(value)

    return " ".join(result)


# =====================================================================
# СЧЁТ CYBER 21
# =====================================================================

def cyber21_score(cards):
    total = 0

    for card in cards:
        rank = normalize_rank(card.get("rank"))

        if rank in CARD_VALUES:
            total += CARD_VALUES[rank]

    return total


# =====================================================================
# ПАРСИНГ КАРТ
# =====================================================================

def parse_cards(text):
    result = []

    if not text:
        return result

    for match in CARD_RE.finditer(text):

        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": suit,
        })

    return result


# =====================================================================
# ПАРСИНГ ИГРЫ
# =====================================================================

def parse_game_message(text):
    """
    Разбирает сообщение вида:
    #N677 ✅5 (6♣️9♦️10♠️) - 1 (A♣️Q♦️Q♥️) #П1 #T6 (ID: ...)
    """

    if not text:
        return None

    number_match = re.search(r"#N(\d+)", text)

    if not number_match:
        return None

    game_number = int(number_match.group(1))

    groups = re.findall(r"\(([^()]*)\)", text)

    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    if not player_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    is_draw = bool(re.search(r"#X\b", text))
    is_ochko = bool(re.search(r"#O\b", text))
    is_finished = bool(re.search(r"✅", text))
    is_r = bool(re.search(r"#R\b", text))

    return {
        "game_number": game_number,
        "game_id": game_id,

        "player_cards": player_cards,
        "dealer_cards": dealer_cards,

        "player_score": player_score,
        "dealer_score": dealer_score,

        "is_draw": is_draw,
        "is_ochko": is_ochko,
        "is_finished": is_finished,
        "is_r": is_r,

        "raw_text": text,

        "received_at": datetime.now(MOSCOW_TZ).isoformat(),
    }


# =====================================================================
# ЛОГ ИГРЫ
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


# =====================================================================
# СДВИГ НОМЕРА ИГРЫ
# =====================================================================

def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % GAME_CYCLE) + 1


# =====================================================================
# ПОЛУЧЕНИЕ КАРТ
# =====================================================================

def get_first_player_card(game):
    player = game.get("player_cards", [])
    if not player:
        return None
    return player[0]


def get_third_player_card(game):
    player = game.get("player_cards", [])
    if len(player) < 3:
        return None
    return player[2]


def get_first_player_rank(game):
    card = get_first_player_card(game)
    if not card:
        return None
    return normalize_rank(card.get("rank"))


def get_third_player_rank(game):
    card = get_third_player_card(game)
    if not card:
        return None
    return normalize_rank(card.get("rank"))


# =====================================================================
# ТРИГГЕР БАККАРЫ
# =====================================================================

def find_trigger_v2(game):
    """
    Триггер в БАККАРЕ.

    Условия:
        1. Игра ЗАВЕРШЕНА (✅).
        2. У игрока РОВНО 3 карты.
        3. 1-я карта — цифра 2-10.
        4. 3-я карта — цифра 2-10.
        5. 1-я карта МЕНЬШЕ 3-й (по значению).

    Прогноз: ранг 3-й карты.
    """

    # 1. Игра завершена
    if not game.get("is_finished"):
        return None

    # 2. Ровно 3 карты
    player = game.get("player_cards", [])

    if len(player) != 3:
        return None

    # 3. Ранги 1-й и 3-й
    first_rank = get_first_player_rank(game)
    third_rank = get_third_player_rank(game)

    if first_rank not in DIGIT_RANKS:
        return None

    if third_rank not in DIGIT_RANKS:
        return None

    # 4. 1-я МЕНЬШЕ 3-й
    if int(first_rank) >= int(third_rank):
        return None

    first_card = get_first_player_card(game)
    third_card = get_third_player_card(game)

    return {
        "trigger_card": card_to_text(first_card),
        "third_card": card_to_text(third_card),
        "predicted_rank": third_rank,
        "target_offset": 3,
    }


# =====================================================================
# ПОСТРОЕНИЕ ПРОГНОЗА
# =====================================================================

def build_prediction_v2(trigger_game, suit_game=None):
    """
    Прогноз = ранг 3-й карты игрока (из триггерной игры).
    suit_game — не используется (оставлено для совместимости).
    """

    if not trigger_game:
        return None

    third_rank = get_third_player_rank(trigger_game)

    if third_rank not in DIGIT_RANKS:
        return None

    return {
        "predicted_rank": third_rank,
        "target_offset": 3,
    }


# =====================================================================
# ПОИСК РАНГА У ИГРОКА И БАНКИРА
# =====================================================================

def find_rank_in_game(game, target_rank):
    """
    Ищет карту с нужным РАНГОМ (любая масть)
    у игрока или банкира.

    Возвращает:
        {"card": "10♠️", "where": "player"} или
        {"card": "10♥️", "where": "dealer"} или
        None
    """

    if not target_rank:
        return None

    target_rank = normalize_rank(target_rank)

    if not target_rank:
        return None

    # Ищем у игрока
    for card in game.get("player_cards", []):

        rank = normalize_rank(card.get("rank"))

        if rank == target_rank:
            return {
                "card": card_to_text(card),
                "where": "player",
            }

    # Ищем у банкира
    for card in game.get("dealer_cards", []):

        rank = normalize_rank(card.get("rank"))

        if rank == target_rank:
            return {
                "card": card_to_text(card),
                "where": "dealer",
            }

    return None


# =====================================================================
# ПОИСК КАРТЫ (старая функция, оставлена для совместимости)
# =====================================================================

def find_card_in_game(game, target_card):
    if not target_card:
        return None

    target_rank_match = re.match(
        r"(10|[2-9AJQK])(♠️|♣️|♦️|♥️)$",
        target_card,
    )

    if not target_rank_match:
        return None

    target_rank = target_rank_match.group(1)
    target_suit = normalize_suit(target_rank_match.group(2))

    if not target_suit:
        return None

    for card in game.get("player_cards", []):

        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))

        if rank == target_rank and suit == target_suit:
            return {
                "card": card_to_text(card),
                "where": "player",
            }

    for card in game.get("dealer_cards", []):

        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))

        if rank == target_rank and suit == target_suit:
            return {
                "card": card_to_text(card),
                "where": "dealer",
            }

    return None