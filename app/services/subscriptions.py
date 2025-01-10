import discord

from app import bot
from app.components.buttons import (
    RemoveDuplicatedReminderButton,
    SyncronizeRemindersButton,
    SyncronizeSubscriptionsButton,
)
from app.data.notifications_twitch import count_streamers_guilds
from app.data.notifications_youtube_video import (
    count_youtube_video_subscription_by_guilds,
)


def get_reminder_subscriptions() -> discord.ui.View:
    reminders = bot.reminder.get_reminders()
    reminders_count_by_streamers = {}
    streamers_by_reminder = {}
    guilds_by_reminder = {}

    for reminder in reminders["data"]:
        streamer = reminder["notes"]
        guilds = count_youtube_video_subscription_by_guilds(streamer)

        if streamer not in reminders_count_by_streamers.keys():
            guilds_by_reminder[reminder["id"]] = guilds

        streamers_by_reminder[reminder["id"]] = streamer
        reminders_count_by_streamers[streamer] = reminders_count_by_streamers.get(streamer, 0) + 1

    return generate_reminders_by_streamer_view(guilds_by_reminder, streamers_by_reminder, reminders_count_by_streamers)


def generate_reminders_by_streamer_view(reminders: dict, streamers_by_reminder: dict, reminders_count_by_streamer: dict) -> discord.Embed:
    view = discord.ui.View()
    view.guilds_by_reminder = reminders
    view.streamers_by_reminder = streamers_by_reminder
    view.reminders_count_by_streamers = reminders_count_by_streamer
    view.add_item(SyncronizeRemindersButton())
    view.add_item(RemoveDuplicatedReminderButton())

    embed = discord.Embed(title="Reminder subscriptions", color=discord.Color.blue())

    for streamer, count in reminders.items():
        streamer_name = streamers_by_reminder[streamer]
        embed.add_field(name=f"{streamer_name} ({reminders_count_by_streamer[streamer_name]})", value=count)

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
    view = discord.ui.View()
    view.guilds_by_streamer = subscriptions
    view.add_item(SyncronizeSubscriptionsButton())

    embed = discord.Embed(title=f"Twitch subscriptions", color=discord.Color.purple())

    for streamer, count in subscriptions.items():
        embed.add_field(name=streamer, value=count)

    view.custom_embed = embed
    return view
