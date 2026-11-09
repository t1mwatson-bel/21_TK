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
        "player_cards": parse_cards(blocks[0]),
        "dealer_cards": parse_cards(blocks[1]),
        "raw": text,
        "received_at": datetime.now(MOSCOW_TZ).isoformat(),
    }

# =====================================================================
# ФЛАГИ АНОМАЛИЙ (#R / #X)
# =====================================================================

# \b после #R/#X — чтобы не поймать #R2, #X10 и т.п.
SKIP_FLAG_RE = re.compile(r"#(?:R|X)\b")

def has_skip_flag(game):
    """True, если в игре есть маркер #R или #X (как отдельный токен)."""
    if not game:
        return False
    return bool(SKIP_FLAG_RE.search(game.get("raw", "")))

# =====================================================================
# ТРИГГЕР: ПЕРВАЯ КАРТА ИГРОКА (С ФИЛЬТРАМИ)
# =====================================================================

def find_trigger(game, prev_game=None, next_game=None):
    """
    Возвращает триггер или None.
    Пропускаем триггер, если #R/#X есть в предыдущей, текущей или следующей игре.
    """
    if game is None:
        return None

    # --- ФИЛЬТР 0: Аномалии в prev / current / next ---
    for g in (prev_game, game, next_game):
        if has_skip_flag(g):
            return None

    player = game.get("player_cards") or []
    if not player:
        return None

    # --- ФИЛЬТР 1: Задвоение масти у игрока ---
    suits = [suit for rank, suit in player]
    if len(suits) != len(set(suits)):
        return None

    # --- ОСНОВНАЯ ЛОГИКА ТРИГГЕРА ---
    rank, suit = player[0]
    predicted_suit = TRIGGER_MAP.get(rank)
    if not predicted_suit:
        return None  # A, 10 — пропуск

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
# ОБРАБОТЧИК ПОТОКА (откладывает на 1 игру, чтобы знать "следующую")
# =====================================================================

class TriggerProcessor:
    """
    Игры приходят по порядку (#N...).
    Чтобы проверить "следующую" игру, держим одну игру в pending
    и обрабатываем её, когда приходит следующая.
    """

    def __init__(self, on_trigger=None):
        self.prev_game = None
        self.pending_game = None
        self.on_trigger = on_trigger
        self._seen_ids = set()

    def feed(self, game):
        if game is None:
            return

        gid = game.get("game_id")

        # Дедуп по game_id (на случай повторов из чата)
        if gid in self._seen_ids:
            return

        # Обрабатываем отложенную игру — теперь у неё есть "следующая"
        if self.pending_game is not None:
            trigger = find_trigger(
                self.pending_game,
                prev_game=self.prev_game,
                next_game=game,
            )
            if trigger:
                self._emit(trigger)

            self._seen_ids.add(self.pending_game["game_id"])
            self.prev_game = self.pending_game

        self.pending_game = game

    def flush(self):
        """Обработать последнюю отложенную игру (без next)."""
        if self.pending_game is None:
            return

        trigger = find_trigger(
            self.pending_game,
            prev_game=self.prev_game,
            next_game=None,
        )
        if trigger:
            self._emit(trigger)

        self._seen_ids.add(self.pending_game["game_id"])
        self.prev_game = self.pending_game
        self.pending_game = None

    def _emit(self, trigger):
        if self.on_trigger:
            self.on_trigger(trigger)
        else:
            print(f"🎯 Триггер: {trigger}", flush=True)

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