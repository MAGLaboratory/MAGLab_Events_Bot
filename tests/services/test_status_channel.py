import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from maglab_events_bot.services.grafana import GrafanaSample, last_active_sample_key
from maglab_events_bot.services.status_channel import StatusChannelReconciler
from maglab_events_bot.services.synoptic import SYNOPTIC_FIELDS, synoptic_image_state_key


def _samples(**values):
    defaults = dict.fromkeys(SYNOPTIC_FIELDS, 0)
    defaults.update(
        {
            "Open Switch": 1,
            "Bay Temp": 30125,
            "Outdoor Temp": 20125,
            "ShopB Temp": 31125,
            "ConfRm Temp": 29125,
            "ElecRm Temp": 32125,
        }
    )
    defaults.update(values)
    sampled_at = datetime.now(timezone.utc)
    return {
        field: GrafanaSample(value=value, sampled_at=sampled_at)
        for field, value in defaults.items()
    }


class StubMessage:
    def __init__(self, message_id, author_id, embeds=None):
        self.id = message_id
        self.author = SimpleNamespace(id=author_id)
        self.embeds = embeds or []
        self.pinned = False
        self.edits = []
        self.pin_count = 0
        self.deleted = False

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        self.embeds = [kwargs["embed"]]
        return self

    async def pin(self, **_kwargs):
        self.pinned = True
        self.pin_count += 1

    async def delete(self):
        self.deleted = True


class StubChannel:
    def __init__(self, bot_user_id):
        self.name = "space-status"
        self.bot_user_id = bot_user_id
        self.sent = []
        self.renames = []

    async def edit(self, **kwargs):
        self.name = kwargs["name"]
        self.renames.append(kwargs["name"])
        return self

    async def send(self, **kwargs):
        message = StubMessage(100, self.bot_user_id, [kwargs["embed"]])
        self.sent.append((message, kwargs))
        return message

    async def pins(self):
        return []


class StubGuild:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, _channel_id):
        return self.channel


class StubBot:
    def __init__(self, user_id=42):
        self.user = SimpleNamespace(id=user_id)


def test_reconcile_creates_one_pinned_dashboard_and_skips_unchanged_state():
    bot = StubBot()
    channel = StubChannel(bot.user.id)
    reconciler = StatusChannelReconciler(
        bot,
        channel_id=123,
        message_id=None,
        rename_channel=True,
        hal_url="https://www.maglaboratory.org/hal",
    )
    samples = _samples()
    state_key = synoptic_image_state_key(samples)

    async def run():
        await reconciler.reconcile(StubGuild(channel), samples, b"png", state_key)
        await reconciler.reconcile(StubGuild(channel), samples, b"new timestamp", state_key)

    asyncio.run(run())

    assert channel.name == "🟢・space-open"
    assert channel.renames == ["🟢・space-open"]
    assert len(channel.sent) == 1
    message, send_kwargs = channel.sent[0]
    assert message.pinned
    assert message.pin_count == 1
    assert message.edits == []
    assert send_kwargs["embed"].title == "MAGLab is OPEN"
    assert send_kwargs["embed"].description.startswith("MAGLab live status • Updated ")
    assert "\n\nPod Bay Door: **Closed**" in send_kwargs["embed"].description
    assert "Front Door: **Closed**" in send_kwargs["embed"].description
    assert "Last Motion: **Unavailable**" in send_kwargs["embed"].description
    assert send_kwargs["embed"].footer.text is None
    assert send_kwargs["embed"].fields == []
    assert send_kwargs["file"].filename == "maglab-status.png"


def test_channel_name_uses_same_calendar_override_as_synoptic_status():
    bot = StubBot()
    channel = StubChannel(bot.user.id)
    reconciler = StatusChannelReconciler(
        bot,
        channel_id=123,
        message_id=None,
        rename_channel=True,
        hal_url="https://www.maglaboratory.org/hal",
    )
    samples = _samples(**{"Open Switch": 0})
    state_key = synoptic_image_state_key(samples, space_is_open_override=True)

    asyncio.run(
        reconciler.reconcile(
            StubGuild(channel),
            samples,
            b"png",
            state_key,
            space_is_open_override=True,
        )
    )

    assert channel.name == "🟢・space-open"
    assert channel.sent[0][1]["embed"].title == "MAGLab is OPEN"


def test_dashboard_formats_latest_general_motion_without_room_name():
    bot = StubBot()
    channel = StubChannel(bot.user.id)
    reconciler = StatusChannelReconciler(
        bot,
        channel_id=123,
        message_id=None,
        rename_channel=False,
        hal_url="https://www.maglaboratory.org/hal",
    )
    samples = _samples()
    samples[last_active_sample_key("Office Motion")] = GrafanaSample(
        value=1,
        sampled_at=datetime(2026, 9, 14, 1, 20, tzinfo=timezone.utc),
    )

    asyncio.run(
        reconciler.reconcile(
            StubGuild(channel),
            samples,
            b"png",
            synoptic_image_state_key(samples),
        )
    )

    description = channel.sent[0][1]["embed"].description
    assert "Last Motion: **9/13/26 6:20 PM**" in description
    assert "PDT" not in description
    assert "Office" not in description


def test_changed_synoptic_state_edits_the_existing_message_and_closes_channel():
    bot = StubBot()
    channel = StubChannel(bot.user.id)
    reconciler = StatusChannelReconciler(
        bot,
        channel_id=123,
        message_id=None,
        rename_channel=True,
        hal_url="https://www.maglaboratory.org/hal",
    )
    open_samples = _samples()
    closed_samples = _samples(**{"Open Switch": 0})

    async def run():
        await reconciler.reconcile(
            StubGuild(channel),
            open_samples,
            b"open",
            synoptic_image_state_key(open_samples),
        )
        await reconciler.reconcile(
            StubGuild(channel),
            closed_samples,
            b"closed",
            synoptic_image_state_key(closed_samples),
        )

    asyncio.run(run())

    message = channel.sent[0][0]
    assert len(channel.sent) == 1
    assert len(message.edits) == 1
    assert message.edits[0]["embed"].title == "MAGLab is CLOSED"
    assert channel.name == "🔴・space-closed"


def test_disabled_reconciler_does_not_touch_discord():
    reconciler = StatusChannelReconciler(
        StubBot(),
        channel_id=None,
        message_id=None,
        rename_channel=True,
        hal_url="https://www.maglaboratory.org/hal",
    )

    asyncio.run(reconciler.reconcile(object(), {}, None, "state"))


def test_message_deletion_invalidates_cached_dashboard():
    bot = StubBot()
    channel = StubChannel(bot.user.id)
    reconciler = StatusChannelReconciler(
        bot,
        channel_id=123,
        message_id=None,
        rename_channel=False,
        hal_url="https://www.maglaboratory.org/hal",
    )
    samples = _samples()
    state_key = synoptic_image_state_key(samples)
    asyncio.run(reconciler.reconcile(StubGuild(channel), samples, b"png", state_key))

    reconciler.invalidate_message(100)
    asyncio.run(reconciler.reconcile(StubGuild(channel), samples, b"png", state_key))

    assert len(channel.sent) == 2
