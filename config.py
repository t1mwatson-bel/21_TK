import os
import pytz

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21") or os.getenv("CHANNEL_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")

# ВТОРОЙ КАНАЛ (куда летят рандомные 30%)
CHANNEL_PROGNOZ_2 = os.getenv("CHAT_ID_21_2") or os.getenv("CHANNEL_PROGNOZ_2")
SECOND_CHANNEL_CHANCE = float(os.getenv("SECOND_CHANNEL_CHANCE", "0.3"))

# =====================================================================
# ФАЙЛЫ
# =====================================================================

DATA_DIR = "/app/data" if os.path.exists("/app/data") else "."
PREDICTIONS_FILE = os.path.join(DATA_DIR, "predictions.json")
OFFSET_FILE = "telegram_offset.txt"
BANK_FILE = "bank.json"
STATS_HTML_FILE = "stats.html"

# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_INTERVAL = 2.0
FINALIZE_WAIT_SECONDS = 30

# =====================================================================
# АЛГОРИТМ ПРОГНОЗА
# =====================================================================

TARGET_OFFSET = 10
SEND_BEFORE_TARGET = 4
DOGON_GAMES = 4

# Маппинг: первая карта игрока → масть прогноза
TRIGGER_MAP = {
    "Q": "♣️",
    "J": "♠️",
    "K": "♠️",
    "2": "♥️",
    "4": "♥️",
    "6": "♥️",
    "8": "♥️",
    "3": "♦️",
    "5": "♦️",
    "7": "♦️",
    "9": "♦️",
}

PREDICTION_TIMEOUT_MINUTES = 20

# =====================================================================
# БАНК
# =====================================================================

START_BALANCE = 70000.0
START_BET = 50.0
BET_MULTIPLIER = 2.5
COEFFICIENT = 1.9

# =====================================================================
# ОЧИСТКА
# =====================================================================

CLEANUP_HOUR = 3
CLEANUP_MINUTE = 0

MAX_GAMES_CACHE = 3000
MAX_PREDICTIONS_STORED = 2000

# =====================================================================
# МАСТИ
# =====================================================================

SUIT_ALIASES = {
    "♠": "♠️", "♠️": "♠️",
    "♣": "♣️", "♣️": "♣️",
    "♦": "♦️", "♦️": "♦️",
    "♥": "♥️", "♥️": "♥️",
}