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
    """Приводит масть к единому виду: ♠️ ♣️ ♦️ ♥️"""

    if suit is None:
        return None

    value = str(suit).strip()

    if value in SUIT_ALIASES:
        return SUIT_ALIASES[value]

    value = value.replace("\ufe0f", "")

    return SUITS.get(value)


def normalize_rank(rank):
    """Приводит ранг карты к единому виду."""

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
    """J=2, Q=3, K=4, A=11."""

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
    """Извлекает карты из одной группы скобок."""

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

    ВАЖНО:
    первые скобки = игрок
    вторые скобки = дилер

    #T полностью игнорируется.
    """

    if not text:
        return None

    # ---------------------------------------------------------------
    # НОМЕР ИГРЫ
    # ---------------------------------------------------------------

    number_match = re.search(r"#N(\d+)", text)

    if not number_match:
        return None

    game_number = int(number_match.group(1))

    # ---------------------------------------------------------------
    # СКOБКИ
    # ---------------------------------------------------------------

    groups = re.findall(r"\(([^()]*)\)", text)

    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    if not player_cards:
        return None

    # ---------------------------------------------------------------
    # ОЧКИ
    # ---------------------------------------------------------------

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    # ---------------------------------------------------------------
    # ID
    # ---------------------------------------------------------------

    id_match = re.search(r"ID:\s*(\d+)", text)

    game_id = id_match.group(1) if id_match else None

    # ---------------------------------------------------------------
    # СЛУЖЕБНЫЕ МЕТКИ
    # ---------------------------------------------------------------

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

    print(
        f"🎮 ИГРА #N{game['game_number']}",
        flush=True,
    )

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
    """Сдвиг номера игры с учётом GAME_CYCLE."""

    return ((int(number) - 1 + int(offset)) % GAME_CYCLE) + 1


# =====================================================================
# НОВЫЙ ТРИГГЕР
# =====================================================================

def find_trigger(game):
    """
    НОВЫЙ АЛГОРИТМ:

    Ищем у игрока последовательность:

        J/Q/K/A → 10

    Например:

        K♣ A♣ 10♣
              ↑
            10♣

    Карта непосредственно перед 10:
        A♣

    Прогноз:
        A + масть десятки = A♣

    ВАЖНО:

    - масть берётся ОТ 10
    - ранг берётся ОТ карты перед 10
    - A тоже является допустимым рангом
    - количество карт игрока определяет задержку:

        2 карты → +20
        3 карты → +30
        4 карты → +40
        5 карт → +50

    Если подходящего J/Q/K/A перед 10 нет — None.

    Если таких комбинаций несколько — берём первую.
    """

    player = game.get("player_cards", [])

    if len(player) < 2:
        return None

    for index in range(1, len(player)):

        current = player[index]
        previous = player[index - 1]

        current_rank = normalize_rank(
            current.get("rank")
        )

        previous_rank = normalize_rank(
            previous.get("rank")
        )

        # -----------------------------------------------------------
        # Текущая карта должна быть 10
        # -----------------------------------------------------------

        if current_rank != "10":
            continue

        # -----------------------------------------------------------
        # Перед 10 допускаем J/Q/K/A
        # -----------------------------------------------------------

        if previous_rank not in {"J", "Q", "K", "A"}:
            continue

        # -----------------------------------------------------------
        # Масть берём именно от 10
        # -----------------------------------------------------------

        predicted_suit = normalize_suit(
            current.get("suit")
        )

        if not predicted_suit:
            continue

        # -----------------------------------------------------------
        # Прогнозируемая карта
        # -----------------------------------------------------------

        predicted_card = (
            f"{previous_rank}{predicted_suit}"
        )

        # -----------------------------------------------------------
        # Количество карт игрока
        # -----------------------------------------------------------

        card_count = len(player)

        target_offset = card_count * 10

        return {
            "trigger_card": card_to_text(previous),
            "ten_card": card_to_text(current),

            "predicted_rank": previous_rank,
            "predicted_suit": predicted_suit,
            "predicted_card": predicted_card,

            "player_card_count": card_count,

            "target_offset": target_offset,
        }

    return None


# =====================================================================
# ПОИСК КАРТЫ У ДИЛЕРА
# =====================================================================

def find_card_in_dealer(game, target_card):
    """
    Ищет КОНКРЕТНУЮ карту только у дилера.

    Например target_card = A♣

    Ищем A♣ только в dealer_cards.
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
    target_suit = normalize_suit(
        target_rank_match.group(2)
    )

    if not target_suit:
        return None

    for card in game.get("dealer_cards", []):

        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))

        if (
            rank == target_rank
            and suit == target_suit
        ):
            return card_to_text(card)

    return None
