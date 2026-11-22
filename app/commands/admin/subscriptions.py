"""The /admin subscriptions screens: the YouTube renewals and the Twitch ones."""

from dataclasses import dataclass
from functools import partial
from typing import Dict, List

import discord

from app import bot
from app.constants import ViewConstants as view_constants
from app.core.threads import to_thread
from app.forms.features.subscriptions import platform_announcers, subscription_feature
from app.ui.buttons import GenericButton


@dataclass(frozen=True)
class ReminderOverview:
    """The YouTube renewals on the reminders API, with their youtubers and followers."""

    guilds_by_reminder: Dict[str, int]
    streamers_by_reminder: Dict[str, str]
    reminders_count_by_streamers: Dict[str, int]
    guilds_by_streamer: Dict[str, int]
    untracked: List[str]


class SubscriptionsView(discord.ui.View):
    """An /admin subscriptions screen: its embed and the buttons that act on it."""

    def __init__(self, embed: discord.Embed) -> None:
        super().__init__(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        self.custom_embed = embed


async def get_reminder_subscriptions() -> SubscriptionsView:
    """The YouTube renewals on the reminders API, told apart by their schedules."""
    return generate_reminders_by_streamer_view(await _reminder_overview())


async def _reminder_overview() -> ReminderOverview:
    youtube = subscription_feature("notifications_youtube_video")
    answer = await to_thread(bot.reminder.get_reminders)
    tracked = await youtube.renewals()
    streamers_by_reminder: Dict[str, str] = {}
    reminders_count_by_streamers: Dict[str, int] = {}
    untracked: List[str] = []

    for reminder in answer["data"]:
        reminder_id = str(reminder["id"])
        streamer = tracked.get(reminder_id)
        if streamer is None:
            if reminder.get("title") != youtube.renewal_title:
                continue
            streamer = reminder["notes"]
            untracked.append(reminder_id)

        streamers_by_reminder[reminder_id] = streamer
        reminders_count_by_streamers[streamer] = (
            reminders_count_by_streamers.get(streamer, 0) + 1
        )

    guilds_by_streamer = {
        streamer: await platform_announcers(youtube.external, streamer)
        for streamer in reminders_count_by_streamers
    }
    return ReminderOverview(
        guilds_by_reminder={
            reminder_id: guilds_by_streamer[streamers_by_reminder[reminder_id]]
            for reminder_id in streamers_by_reminder
            if reminder_id not in untracked
        },
        streamers_by_reminder=streamers_by_reminder,
        reminders_count_by_streamers=reminders_count_by_streamers,
        guilds_by_streamer=guilds_by_streamer,
        untracked=untracked,
    )


async def sync_reminders(
    overview: ReminderOverview, interaction: discord.Interaction
) -> None:
    """Delete every YouTube renewal no server follows any more."""
    await interaction.response.defer(thinking=True)

    reminders_deleted = []
    for reminder, count in overview.guilds_by_reminder.items():
        if count > 0:
            continue

        await subscription_feature("notifications_youtube_video").forget_renewal(
            reminder
        )
        reminders_deleted.append(overview.streamers_by_reminder[reminder])

    await interaction.followup.send(
        content=(
            f"Deleting {', '.join(reminders_deleted)} reminders. Total of "
            f"{len(reminders_deleted)} reminders deleted."
        ),
    )


async def remove_duplicated_reminders(
    overview: ReminderOverview, interaction: discord.Interaction
) -> None:
    """Delete the renewals Keiko does not track, keeping one per youtuber."""
    await interaction.response.defer(thinking=True)

    removed_reminders = 0
    removed_by_streamers: Dict[str, int] = {}
    for streamer, count in overview.reminders_count_by_streamers.items():
        if count == 1:
            continue

        extras = [
            reminder
            for reminder in overview.untracked
            if overview.streamers_by_reminder[reminder] == streamer
        ]
        if len(extras) == count:
            extras = extras[1:]

        for reminder in extras:
            await to_thread(bot.reminder.delete_reminder, reminder)
            removed_by_streamers[streamer] = removed_by_streamers.get(streamer, 0) + 1
            removed_reminders += 1

    message = "\n".join(
        [
            f"Deleted {count} reminders from {streamer}"
            for streamer, count in removed_by_streamers.items()
        ]
    )

    await interaction.followup.send(
        content=f"{message}\nTotal of {removed_reminders} reminders deleted",
    )


async def sync_subscriptions(
    guilds_by_streamer: Dict[str, int], interaction: discord.Interaction
) -> None:
    """Unsubscribe from every Twitch streamer no server follows any more."""
    await interaction.response.defer(thinking=True)

    unsubscribed_streamers = []
    for streamer, count in guilds_by_streamer.items():
        if count > 0:
            continue

        await subscription_feature("notifications_twitch").unsubscribe(
            interaction, {"streamer": {"value": streamer}}
        )
        unsubscribed_streamers.append(streamer)

    await interaction.followup.send(
        content=(
            f"Unsubscribed from {', '.join(unsubscribed_streamers)}. Total "
            f"of {len(unsubscribed_streamers)} streamers."
        ),
    )


def generate_reminders_by_streamer_view(
    overview: ReminderOverview,
) -> SubscriptionsView:
    """The renewals screen: a field per youtuber, the sync and duplicates buttons."""
    embed = discord.Embed(title="Reminder subscriptions", color=discord.Color.blue())
    for streamer_name, count in overview.reminders_count_by_streamers.items():
        embed.add_field(
            name=f"{streamer_name} ({count})",
            value=overview.guilds_by_streamer[streamer_name],
        )

    view = SubscriptionsView(embed)
    view.add_item(
        GenericButton(
            "Syncronize Reminders",
            partial(sync_reminders, overview),
            discord.ButtonStyle.grey,
            emoji="🔄",
        )
    )
    view.add_item(
        GenericButton(
            "Remove Duplicated Reminders",
            partial(remove_duplicated_reminders, overview),
            discord.ButtonStyle.red,
            emoji="🗑️",
        )
    )
    return view


async def get_twitch_subscriptions() -> SubscriptionsView:
    """The Twitch subscriptions, each streamer with how many servers follow it."""
    twitch = subscription_feature("notifications_twitch")
    subscriptions = (await to_thread(bot.twitch.get_subscriptions))["data"]
    guilds_by_streamer = {}

    for subscription in subscriptions:
        user_id = subscription["condition"]["broadcaster_user_id"]
        streamer = await to_thread(bot.twitch.get_user_info_by_id, user_id)
        count = await twitch.announcers(streamer["login"])
        guilds_by_streamer[streamer["login"]] = count

    return generate_guilds_by_twitch_subscription_view(guilds_by_streamer)


def generate_guilds_by_twitch_subscription_view(
    subscriptions: Dict[str, int],
) -> SubscriptionsView:
    """The Twitch screen: one field per streamer, and the sync button."""
    embed = discord.Embed(title="Twitch subscriptions", color=discord.Color.purple())
    for streamer, count in subscriptions.items():
        embed.add_field(name=streamer, value=count)

    view = SubscriptionsView(embed)
    view.add_item(
        GenericButton(
            "Syncronize Subscriptions",
            partial(sync_subscriptions, subscriptions),
            discord.ButtonStyle.grey,
            emoji="🔄",
        )
    )
    return view
