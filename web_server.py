# -*- coding: utf-8 -*-

import os
import json
import http.server
import socketserver
from urllib.parse import urlparse

from config import (
    STATS_HTML_FILE,
    PREDICTIONS_FILE,
)

from stats import get_full_stats


# ============================================================
# НАСТРОЙКИ
# ============================================================

PORT = int(os.getenv("PORT", 8000))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# ЗАГРУЗКА ПРОГНОЗОВ
# ============================================================

def load_predictions():
    """
    Загружает прогнозы из predictions.json.
    Поддерживает список и словарь с ключом predictions.
    """

    try:
        if not os.path.exists(PREDICTIONS_FILE):
            print(
                f"⚠️ Файл прогнозов не найден: {PREDICTIONS_FILE}",
                flush=True
            )
            return []

        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            predictions = data.get("predictions", [])

            if isinstance(predictions, list):
                return predictions

        print("⚠️ Неизвестный формат predictions.json", flush=True)
        return []

    except Exception as e:
        print(f"⚠️ Ошибка загрузки прогнозов: {e}", flush=True)
        return []


# ============================================================
# ВЕБ-СЕРВЕР
# ============================================================

class StatsHandler(http.server.SimpleHTTPRequestHandler):

    def send_json(self, data, status=200):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):

        path = urlparse(self.path).path

        # ----------------------------------------------------
        # API СТАТИСТИКИ
        # ----------------------------------------------------

        if path == "/api/stats":

            try:
                predictions = load_predictions()

                result = get_full_stats(predictions)

                self.send_json(result)

            except Exception as e:
                print(
                    f"❌ Ошибка формирования статистики: {e}",
                    flush=True
                )

                self.send_json({
                    "error": str(e),
                    "summary": {},
                    "dogons": [],
                    "suits": [],
                    "daily": [],
                    "last": [],
                }, status=500)

            return

        # ----------------------------------------------------
        # ПРОВЕРКА РАБОТЫ СЕРВЕРА
        # ----------------------------------------------------

        if path == "/health":

            self.send_json({
                "status": "ok",
                "service": "OLD_GROUP",
            })

            return

        # ----------------------------------------------------
        # ГЛАВНАЯ СТРАНИЦА
        # ----------------------------------------------------

        if path == "/":

            self.path = "/" + STATS_HTML_FILE

        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        pass


# ============================================================
# ЗАПУСК
# ============================================================

def start_web_server():

    os.chdir(BASE_DIR)

    class ThreadingServer(
        socketserver.ThreadingMixIn,
        socketserver.TCPServer
    ):
        allow_reuse_address = True
        daemon_threads = True

    try:

        with ThreadingServer(
            ("0.0.0.0", PORT),
            StatsHandler
        ) as httpd:

            print(
                f"🌐 OLD_GROUP WEB SERVER: порт {PORT}",
                flush=True
            )

            print(
                "📊 API статистики: /api/stats",
                flush=True
            )

            httpd.serve_forever()

    except Exception as e:

        print(
            f"❌ Ошибка веб-сервера: {e}",
            flush=True
        )