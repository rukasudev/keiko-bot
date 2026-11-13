import asyncio
from typing import Any, Dict

import discord

from app import bot
from app.components.buttons import (
    RemoveDuplicatedReminderButton,
    SyncronizeRemindersButton,
    SyncronizeSubscriptionsButton,
)
from app.constants import ViewConstants as view_constants
from app.data.keiko.schedules import find_schedules_by_kind, schedule_subject
from app.services.notifications_twitch import count_streamers_guilds
from app.services.notifications_youtube_video import (
    count_youtube_video_subscription_by_guilds,
    delete_reminder_by_id,
)


async def get_reminder_subscriptions() -> discord.ui.View:
    """The YouTube renewals on the reminders API, told apart by their schedules."""
    overview = await asyncio.to_thread(_reminder_overview)
    view = generate_reminders_by_streamer_view(
        overview["guilds_by_reminder"],
        overview["streamers_by_reminder"],
        overview["reminders_count_by_streamers"],
        overview["guilds_by_streamer"],
    )
    view.untracked_reminders = overview["untracked"]
    return view


def _reminder_overview() -> Dict[str, Any]:
    reminders = bot.reminder.get_reminders()
    tracked = {
        schedule["reminder_id"]: schedule_subject(schedule)[0]
        for schedule in find_schedules_by_kind("youtube_renewal")
    }
    streamers_by_reminder = {}
    reminders_count_by_streamers = {}
    untracked = []

    for reminder in reminders["data"]:
        reminder_id = str(reminder["id"])
        streamer = tracked.get(reminder_id)
        if streamer is None:
            if reminder.get("title") != "youtube_notification":
                continue
            streamer = reminder["notes"]
            untracked.append(reminder_id)

        streamers_by_reminder[reminder_id] = streamer
        reminders_count_by_streamers[streamer] = reminders_count_by_streamers.get(streamer, 0) + 1

    guilds_by_streamer = {
        streamer: count_youtube_video_subscription_by_guilds(streamer)
        for streamer in reminders_count_by_streamers
    }
    return {
        "guilds_by_reminder": {
            reminder_id: guilds_by_streamer[streamers_by_reminder[reminder_id]]
            for reminder_id in streamers_by_reminder
            if reminder_id not in untracked
        },
        "streamers_by_reminder": streamers_by_reminder,
        "reminders_count_by_streamers": reminders_count_by_streamers,
        "guilds_by_streamer": guilds_by_streamer,
        "untracked": untracked,
    }


def delete_renewal(reminder_id: str) -> None:
    """Delete a YouTube renewal on the reminders API, then its schedule."""
    bot.reminder.delete_reminder(reminder_id)
    delete_reminder_by_id(reminder_id)


def generate_reminders_by_streamer_view(
    reminders: dict,
    streamers_by_reminder: dict,
    reminders_count_by_streamer: dict,
    guilds_by_streamer: dict,
) -> discord.ui.View:
    view = discord.ui.View(timeout=view_constants.LONG_TIMEOUT_SECONDS)
    view.guilds_by_reminder = reminders
    view.streamers_by_reminder = streamers_by_reminder
    view.reminders_count_by_streamers = reminders_count_by_streamer
    view.untracked_reminders = []
    view.add_item(SyncronizeRemindersButton())
    view.add_item(RemoveDuplicatedReminderButton())

    embed = discord.Embed(title="Reminder subscriptions", color=discord.Color.blue())

    for streamer_name, count in reminders_count_by_streamer.items():
        embed.add_field(
            name=f"{streamer_name} ({count})", value=guilds_by_streamer[streamer_name]
        )

    view.custom_embed = embed
    return view

def get_twitch_subscriptions() -> discord.ui.View:
    subscriptions = bot.twitch.get_subscriptions()["data"]
    guils_by_streamer = {}

    for subscription in subscriptions:
        user_id = subscription["condition"]["broadcaster_user_id"]
        streamer = bot.twitch.get_user_info_by_id(user_id)
        count = count_streamers_guilds(streamer["login"])
        guils_by_streamer[streamer["login"]] = count

    return generate_guilds_by_twitch_subscription_view(guils_by_streamer)

def generate_guilds_by_twitch_subscription_view(subscriptions: dict) -> discord.Embed:
    view = discord.ui.View(timeout=view_constants.LONG_TIMEOUT_SECONDS)
    view.guilds_by_streamer = subscriptions
    view.add_item(SyncronizeSubscriptionsButton())

    embed = discord.Embed(title=f"Twitch subscriptions", color=discord.Color.purple())

    for streamer, count in subscriptions.items():
        embed.add_field(name=streamer, value=count)

    view.custom_embed = embed
    return view
