import asyncio
import sys

from telegram import Bot
from telegram.error import TelegramError

from config import BOT_TOKEN, SOURCE_CHANNEL


def _channel_arg(argv):
    if len(argv) < 2:
        return SOURCE_CHANNEL
    raw = argv[1].strip()
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        print("Usage: python testbot.py [@username | channel_id]")
        return None


async def test(channel_id):
    bot = Bot(BOT_TOKEN)
    async with bot:
        me = await bot.get_me()
        print(f"bot: @{me.username} id={me.id}")
        try:
            chat = await bot.get_chat(channel_id)
        except TelegramError as exc:
            print(f"get_chat failed: {exc}")
            return

        print(f"channel: {chat.title} type={chat.type} id={chat.id}")
        try:
            admins = await bot.get_chat_administrators(chat.id)
        except TelegramError as exc:
            print(f"admin check failed: {exc}")
            return

        bot_found = False
        for admin in admins:
            user = admin.user
            marker = ""
            if user.id == me.id:
                bot_found = True
                marker = "  <-- this bot"
            print(
                f"admin: {user.first_name} id={user.id} "
                f"bot={user.is_bot} status={admin.status}{marker}"
            )
        if not bot_found:
            print("this bot is not an administrator of the channel")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    channel_id = _channel_arg(sys.argv)
    if channel_id is None:
        return
    asyncio.run(test(channel_id))


if __name__ == "__main__":
    main()
