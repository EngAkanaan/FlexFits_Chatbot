from __future__ import annotations

import argparse

from local_chat import run_local_chat
from telegram_bot import main as run_telegram_bot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FLEX Fits V2 runner")
    parser.add_argument(
        "--mode",
        choices=["local", "telegram"],
        default="local",
        help="Run local terminal chat or Telegram long polling.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "telegram":
        run_telegram_bot()
        return
    run_local_chat()


if __name__ == "__main__":
    main()
