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
    r"(10|[2-9AJQK])"
    r"(♠|♣|♦|♥)"
    r"\ufe0f?"
)


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
    #N1247. 23(10♠K♥9♥) - ✅18(A♣7♥) #T41 (ID: 759233499)
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

    return {
        "game_number": game_number,
        "game_id": game_id,

        "player_cards": player_cards,
        "dealer_cards": dealer_cards,

        "player_score": player_score,
        "dealer_score": dealer_score,

        "is_draw": is_draw,
        "is_ochko": is_ochko,

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

    print("────────────────────────────────────", flush=True)


# =====================================================================
# СДВИГ НОМЕРА ИГРЫ
# =====================================================================

def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % GAME_CYCLE) + 1


# =====================================================================
# ПОЛУЧЕНИЕ ПЕРВОЙ КАРТЫ ИГРОКА
# =====================================================================

def get_first_player_card(game):
    """
    Возвращает первую карту игрока (dict с rank и suit).
    Если карт нет — None.
    """

    player = game.get("player_cards", [])

    if not player:
        return None

    return player[0]


def get_first_player_rank(game):
    """
    Возвращает ранг первой карты игрока (например, "Q").
    Если карт нет — None.
    """

    card = get_first_player_card(game)

    if not card:
        return None

    return normalize_rank(card.get("rank"))


def get_first_player_suit(game):
    """
    Возвращает масть первой карты игрока (например, "♠️").
    Если карт нет — None.
    """

    card = get_first_player_card(game)

    if not card:
        return None

    return normalize_suit(card.get("suit"))


# =====================================================================
# НОВЫЙ ТРИГГЕР (v2): первая карта игрока = J/Q/K/A
# =====================================================================

def find_trigger_v2(game):
    """
    НОВЫЙ АЛГОРИТМ (v2):

    Триггер: первая карта игрока — J/Q/K/A.

    Возвращает:
        {
            "trigger_card": "Q♦️",   # первая карта игрока
            "rank": "Q",               # ранг (для прогноза)
            "target_offset": 2,        # всегда +2
        }

    Если триггера нет — None.
    """

    first_rank = get_first_player_rank(game)

    if not first_rank:
        return None

    if first_rank not in {"J", "Q", "K", "A"}:
        return None

    first_card = get_first_player_card(game)

    return {
        "trigger_card": card_to_text(first_card),
        "rank": first_rank,
        "target_offset": 2,
    }


# =====================================================================
# ПОСТРОЕНИЕ ПРОГНОЗА (v2): rank от триггера + suit от триггер-3
# =====================================================================

def build_prediction_v2(trigger_game, suit_game):
    """
    Строит прогноз по новому алгоритму.

    trigger_game — игра, где сработал триггер (первая карта J/Q/K/A).
    suit_game    — игра (триггер − 3), откуда берём масть.

    Возвращает:
        {
            "predicted_card": "Q♠️",
            "predicted_rank": "Q",
            "predicted_suit": "♠️",
            "target_offset": 2,
        }

    Если что-то не так — None.
    """

    if not trigger_game or not suit_game:
        return None

    # Ранг — от триггерной игры
    rank = get_first_player_rank(trigger_game)

    if rank not in {"J", "Q", "K", "A"}:
        return None

    # Масть — от игры триггер − 3
    suit = get_first_player_suit(suit_game)

    if not suit:
        return None

    predicted_card = f"{rank}{suit}"

    return {
        "predicted_card": predicted_card,
        "predicted_rank": rank,
        "predicted_suit": suit,
        "target_offset": 2,
    }


# =====================================================================
# ПОИСК КАРТЫ У ИГРОКА И ДИЛЕРА
# =====================================================================

def find_card_in_game(game, target_card):
    """
    Ищет КОНКРЕТНУЮ карту и у игрока, и у дилера.

    Возвращает:
        {"card": "K♦️", "where": "player"} или
        {"card": "K♦️", "where": "dealer"} или
        None
    """

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

    # Ищем у игрока
    for card in game.get("player_cards", []):

        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))

        if rank == target_rank and suit == target_suit:
            return {
                "card": card_to_text(card),
                "where": "player",
            }

    # Ищем у дилера
    for card in game.get("dealer_cards", []):

        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))

        if rank == target_rank and suit == target_suit:
            return {
                "card": card_to_text(card),
                "where": "dealer",
            }

    return None
