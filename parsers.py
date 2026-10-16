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
# РАНГИ ДЛЯ ТРИГГЕРА — только старшие (J/Q/K/A)
# =====================================================================

TRIGGER_RANKS = {"J", "Q", "K", "A"}

# Старшие ранги (для проверки задвоений)
HIGH_RANKS = {"J", "Q", "K", "A"}


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
    #N377 24(Q♣️Q♥️8♦️10♠️) - ✅0() #T24 (ID: ...)
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
# ПОЛУЧЕНИЕ КАРТ ИГРОКА
# =====================================================================

def get_first_player_card(game):
    player = game.get("player_cards", [])
    if not player:
        return None
    return player[0]


def get_first_player_rank(game):
    card = get_first_player_card(game)
    if not card:
        return None
    return normalize_rank(card.get("rank"))


# =====================================================================
# ПРОВЕРКА ЗАДВОЕНИЯ СТАРШИХ РАНГОВ
# =====================================================================

def has_high_rank_duplicate(game):
    """
    True, если у игрока есть ДВЕ или больше карты одного
    старшего ранга (J/Q/K/A).

    Цифровые задвоения (6+6, 7+7 и т.д.) — НЕ считаются.
    """

    player = game.get("player_cards", [])

    high_ranks_seen = {}

    for card in player:
        rank = normalize_rank(card.get("rank"))

        if rank not in HIGH_RANKS:
            continue

        high_ranks_seen[rank] = high_ranks_seen.get(rank, 0) + 1

        if high_ranks_seen[rank] >= 2:
            return True

    return False


# =====================================================================
# ТРИГГЕР 21
# =====================================================================

def find_trigger_21(game):
    """
    Триггер в 21.

    Условия:
        1. Игра ЗАВЕРШЕНА (✅).
        2. У игрока ОТ 3 ДО 5 карт.
        3. У дилера РОВНО 0 карт (пустые скобки).
        4. Первая карта игрока — ранг J, Q, K или A.
        5. НЕТ задвоения старших рангов (J/Q/K/A).
        6. НЕТ #O (очко).

    Прогноз: РАНГ первой карты игрока.
    """

    # 1. Игра завершена
    if not game.get("is_finished"):
        return None

    # 2. У игрока 3-5 карт
    player = game.get("player_cards", [])

    if len(player) < 3 or len(player) > 5:
        return None

    # 3. У дилера 0 карт
    dealer = game.get("dealer_cards", [])

    if len(dealer) != 0:
        return None

    # 4. Первая карта — только J/Q/K/A
    first_rank = get_first_player_rank(game)

    if first_rank not in TRIGGER_RANKS:
        return None

    # 5. Нет задвоения старших рангов
    if has_high_rank_duplicate(game):
        return None

    # 6. Нет очка (#O)
    if game.get("is_ochko"):
        return None

    first_card = get_first_player_card(game)

    return {
        "trigger_card": card_to_text(first_card),
        "predicted_rank": first_rank,
    }


# =====================================================================
# ПОИСК РАНГА У ИГРОКА (результат прогноза)
# =====================================================================

def find_rank_in_player(game, target_rank):
    """
    Ищет карту с нужным РАНГОМ (любая масть) ТОЛЬКО у игрока.

    Возвращает:
        {"card": "Q♣️", "where": "player"} или
        None
    """

    if not target_rank:
        return None

    target_rank = normalize_rank(target_rank)

    if not target_rank:
        return None

    for card in game.get("player_cards", []):

        rank = normalize_rank(card.get("rank"))

        if rank == target_rank:
            return {
                "card": card_to_text(card),
                "where": "player",
            }

    return None