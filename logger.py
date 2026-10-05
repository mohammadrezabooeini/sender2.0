import logging
import os
import re
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


class _RedactingFormatter(logging.Formatter):
    def format(self, record):
        return _TOKEN_RE.sub("bot<redacted>", super().format(record))


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        return

    formatter = _RedactingFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "bot.log"),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    try:
        stream.stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    root.addHandler(file_handler)
    root.addHandler(stream)

    # httpx logs the full request URL, which includes the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


setup_logging()
logger = logging.getLogger("signalbot")
