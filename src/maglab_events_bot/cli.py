"""Command-line interface for MAGLab Events Bot tasks."""

from __future__ import annotations

import argparse
import asyncio
import sys

import pendulum

from maglab_events_bot.bot import main as run_bot
from maglab_events_bot.config import get_settings
from maglab_events_bot.services.grafana import fetch_grafana_open_status
from maglab_events_bot.services.hal import fetch_hal_status
from maglab_events_bot.services.synoptic import generate_synoptic_image
from maglab_events_bot.utils.http import build_session


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

    subparsers.add_parser(
        "health-check",
        help="Ping HAL and Grafana once to verify connectivity before running the bot",
    )

    return parser


async def _run_health_check() -> int:
    settings = get_settings()
    tz = pendulum.timezone(settings.timezone)
    session = build_session()

    ok = True
    try:
        hal_status = await fetch_hal_status(str(settings.hal_url), tz, session=session)
        if hal_status is None:
            print("HAL check: FAILED (no response)", file=sys.stderr)
            ok = False
        else:
            status_text = "OPEN" if hal_status.is_open else "CLOSED"
            print(f"HAL check: {status_text} (sensors={len(hal_status.sensors)})")

        grafana_status = await fetch_grafana_open_status(
            base_url=str(settings.grafana_base_url),
            alert_name=settings.grafana_alert_name,
            alerts_endpoint=settings.grafana_alerts_endpoint,
            username=settings.grafana_username,
            password=settings.grafana_password,
            verify_tls=settings.grafana_verify_ssl,
            session=session,
        )
        if grafana_status is None:
            print("Grafana check: FAILED (see logs for details)", file=sys.stderr)
            ok = False
        else:
            state = "FIRING (OPEN)" if grafana_status else "RESOLVED (CLOSED)"
            print(f"Grafana check: {state}")
    finally:
        session.close()

    return 0 if ok else 1


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
    if args.command == "health-check":
        return asyncio.run(_run_health_check())

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
