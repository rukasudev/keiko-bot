"""YouTube lives: a channel told when a YouTube channel goes live, declared once."""

from app.commands.youtube_live.service import (
    FEATURE,
    announce_started_live,
    end_live,
    read_due_lives,
    schedule_live,
)
from app.constants import FeaturePermissions
from app.core.commands import Command

COMMAND = Command(
    key="notifications_youtube_live",
    feature=FEATURE,
    group="notifications",
    on_setup=True,
    emoji="🎥",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    webhooks={
        "youtube.live.started": announce_started_live,
        "youtube.live.scheduled": schedule_live,
        "youtube.live.ended": end_live,
    },
    schedules={"youtube_live": read_due_lives},
)
