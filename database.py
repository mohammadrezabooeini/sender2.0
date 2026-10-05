import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "bot.db")


@contextmanager
def connect():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channels(
                id INTEGER PRIMARY KEY,
                channel_id INTEGER UNIQUE,
                last_sent INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


def add_channel(channel_id, last_sent=0):
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO channels(channel_id, last_sent) VALUES(?, ?)",
                (int(channel_id), int(last_sent)),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def remove_channel(channel_id):
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM channels WHERE channel_id=?",
            (int(channel_id),),
        )
        return cursor.rowcount > 0


def get_channel_details():
    with connect() as conn:
        rows = conn.execute(
            "SELECT channel_id, last_sent FROM channels ORDER BY channel_id"
        ).fetchall()
    return [(int(row["channel_id"]), int(row["last_sent"] or 0)) for row in rows]


def get_channels():
    return [channel_id for channel_id, _last_sent in get_channel_details()]


def update_last_sent(channel_id, message_id):
    with connect() as conn:
        conn.execute(
            """
            UPDATE channels
            SET last_sent=?
            WHERE channel_id=? AND last_sent<?
            """,
            (int(message_id), int(channel_id), int(message_id)),
        )


def remember_source_message(message_id):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES('last_source_message_id', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            WHERE CAST(excluded.value AS INTEGER) > CAST(settings.value AS INTEGER)
            """,
            (str(int(message_id)),),
        )


def get_last_source_message_id():
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='last_source_message_id'"
        ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0
