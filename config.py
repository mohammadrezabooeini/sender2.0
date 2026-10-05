import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _required(name):
    value = os.getenv(name)
    if value is None or not str(value).strip():
        raise RuntimeError(f"{name} is missing. Add it to .env")
    return str(value).strip().strip('"').strip("'")


def _required_int(name):
    raw = _required(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


BOT_TOKEN = _required("BOT_TOKEN")
SOURCE_CHANNEL = _required_int("SOURCE_CHANNEL")
ADMIN_ID = _required_int("ADMIN_ID")


def is_admin(user_id):
    return user_id == ADMIN_ID
