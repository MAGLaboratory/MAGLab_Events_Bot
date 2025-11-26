"""Command-line interface for MAGLab Events Bot tasks."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import pendulum

from maglab_events_bot.bot import main as run_bot
from maglab_events_bot.config import get_settings
from maglab_events_bot.logging import configure_logging
from maglab_events_bot.services.grafana import fetch_grafana_open_status
from maglab_events_bot.services.hal import fetch_hal_status
from maglab_events_bot.services.synoptic import generate_synoptic_image
from maglab_events_bot.utils.http import build_aiohttp_client, build_session

logger = logging.getLogger(__name__)


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
    configure_logging()
    session = await build_aiohttp_client(verify_ssl=settings.grafana_verify_ssl)

    ok = True
    try:
        hal_status = await fetch_hal_status(str(settings.hal_url), tz, session=session)
        if hal_status is None:
            logger.error("health.hal_failed", extra={"url": str(settings.hal_url)})
            ok = False
        else:
            status_text = "OPEN" if hal_status.is_open else "CLOSED"
            logger.info(
                "health.hal_ok",
                extra={"status": status_text, "sensor_count": len(hal_status.sensors)},
            )

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
            logger.error(
                "health.grafana_failed",
                extra={
                    "url": str(settings.grafana_base_url),
                    "alert": settings.grafana_alert_name,
                },
            )
            ok = False
        else:
            state = "FIRING (OPEN)" if grafana_status else "RESOLVED (CLOSED)"
            logger.info(
                "health.grafana_ok",
                extra={
                    "alert": settings.grafana_alert_name,
                    "state": state,
                },
            )
    finally:
        await session.close()

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
        session = build_session(timeout=settings.synoptic_http_timeout_seconds)
        result = generate_synoptic_image(
            str(settings.hal_url),
            "maglab-synoptic-view",
            output_path,
            session=session,
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
