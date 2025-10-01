"""Command-line interface for MAGLab Events Bot tasks."""
from __future__ import annotations

import argparse
import sys

from maglab_events_bot.bot import main as run_bot
from maglab_events_bot.config import get_settings
from maglab_events_bot.services.synoptic import generate_synoptic_image


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MAGLab Events Bot utilities")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run-bot", help="Run the Discord bot with all cogs enabled")

    generate_parser = subparsers.add_parser(
        "generate-synoptic", help="Generate the synoptic PNG without running the bot"
    )
    generate_parser.add_argument(
        "--output",
        dest="output",
        help="Override output path for the synoptic image",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-bot":
        run_bot()
        return 0
    if args.command == "generate-synoptic":
        settings = get_settings()
        output_path = settings.synoptic_output_path if args.output is None else args.output
        result = generate_synoptic_image(
            str(settings.hal_url),
            "maglab-synoptic-view",
            output_path,
        )
        if result is None:
            print("Failed to generate synoptic image", file=sys.stderr)
            return 1
        print(f"Synoptic image written to {result}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
