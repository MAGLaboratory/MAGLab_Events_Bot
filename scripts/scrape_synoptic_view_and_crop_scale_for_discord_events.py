"""Generate the synoptic status image using the packaged service."""
from maglab_events_bot.config import get_settings
from maglab_events_bot.services.synoptic import generate_synoptic_image


def main() -> None:
    settings = get_settings()
    generate_synoptic_image(
        str(settings.hal_url),
        "maglab-synoptic-view",
        settings.synoptic_output_path,
    )


if __name__ == "__main__":
    main()
