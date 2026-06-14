"""MAGLab Events Bot package."""

__all__ = ["build_bot", "main"]


def build_bot(*args, **kwargs):
    from maglab_events_bot.bot import build_bot as _build_bot

    return _build_bot(*args, **kwargs)


def main(*args, **kwargs):
    from maglab_events_bot.bot import main as _main

    return _main(*args, **kwargs)
