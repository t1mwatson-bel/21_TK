import re
from datetime import datetime

from config import MOSCOW_TZ, TRIGGER_MAP, TARGET_OFFSET, SUIT_ALIASES

# =====================================================================
# МАСТИ
# =====================================================================

def normalize_suit(suit):
    if not suit:
        return ""
    return SUIT_ALIASES.get(suit, suit)

# =====================================================================
# ПАРСИНГ КАРТ
# =====================================================================

CARD_RE = re.compile(r"(10|[2-9JQKA])([♠♣♦♥]️?)")

def parse_cards(text):
    """'(9♥️A♦️5♠️)' → [('9','♥️'), ('A','♦️'), ('5','♠️')]"""
    if not text:
        return []
    return [
        (m.group(1).upper(), normalize_suit(m.group(2)))
        for m in CARD_RE.finditer(text)
    ]

# =====================================================================
# ПАРСИНГ ИГРЫ
# =====================================================================

GAME_ID_RE = re.compile(r"#N(\d+)")
BLOCK_RE = re.compile(r"\(([^)]+)\)")

def parse_game_message(text):
    """
    '#N352 2 (2♠️10♣️) - ✅9 (K♣️9♠️) #П2 #T11'
    → {game_id, player_cards, dealer_cards, raw, received_at}
    """
    if not text:
        return None

    m = GAME_ID_RE.search(text)
    if not m:
        return None
    game_id = int(m.group(1))

    blocks = BLOCK_RE.findall(text)
    if len(blocks) < 2:
        return None

    return {
        "game_id": game_id,
        "player_cards": parse_cards(blocks[0]),   # левая скобка = игрок
        "dealer_cards": parse_cards(blocks[1]),   # правая = дилер
        "raw": text,
        "received_at": datetime.now(MOSCOW_TZ).isoformat(),
    }

# =====================================================================
# ТРИГГЕР: ПЕРВАЯ КАРТА ИГРОКА (С ФИЛЬТРАМИ)
# =====================================================================

def find_trigger(game):
    # --- ФИЛЬТР 1: Исключаем аномалии и ничьи (#R, #X) ---
    raw_text = game.get("raw", "")
    if "#R" in raw_text or "#X" in raw_text:
        return None

    player = game.get("player_cards") or []
    if not player:
        return None

    # --- ФИЛЬТР 2: Задвоение масти у игрока ---
    suits = [suit for rank, suit in player]
    if len(suits) != len(set(suits)):
        return None  # Если есть дубликаты мастей — пропускаем

    # --- ОСНОВНАЯ ЛОГИКА ТРИГГЕРА ---
    rank, suit = player[0]
    predicted_suit = TRIGGER_MAP.get(rank)
    if not predicted_suit:
        return None   # A, 10 — пропуск

    trigger_number = game["game_id"]
    return {
        "trigger_number": trigger_number,
        "target_number": trigger_number + TARGET_OFFSET,
        "predicted_suit": predicted_suit,
        "trigger_card": f"{rank}{suit}",
    }

# =====================================================================
# ПОИСК МАСТИ У ИГРОКА
# =====================================================================

def find_suit_in_player(game, suit):
    target = normalize_suit(suit)
    for rank, s in game.get("player_cards") or []:
        if normalize_suit(s) == target:
            return f"{rank}{s}"
    return None

# =====================================================================
# ХЕЛПЕРЫ
# =====================================================================

def add_game_offset(game_number, offset):
    return game_number + offset

def log_game(game):
    p = game.get("player_cards", [])
    d = game.get("dealer_cards", [])
    ps = " ".join(f"{r}{s}" for r, s in p)
    ds = " ".join(f"{r}{s}" for r, s in d)
    print(f"🎲 #N{game['game_id']}  игрок: ({ps})  дилер: ({ds})", flush=True)
