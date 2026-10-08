"""Entry point: python -m telegram_bot"""
from telegram_bot.bot import build_app


def main() -> None:
  app = build_app()
  print("War Room Telegram Bot starting... (Ctrl+C to stop)")
  app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
  main()
