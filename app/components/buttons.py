from typing import Any, Callable

import discord

from app.components.embed import response_embed
from app.constants import LogTypes as logconstants
from app.services.cache import increment_redis_key
from app.services.utils import get_command_by_key, ml, parse_locale


class BackButton(discord.ui.Button):
    def __init__(
        self, view: discord.ui.View, embed: discord.Embed, locale: str
    ) -> None:
        self.old_view = view
        self.old_embed = embed
        self.desc = ml("buttons.back.desc", locale=locale)
        super().__init__(
            label=ml("buttons.back.label", locale=locale),
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(
            embed=self.old_embed, view=self.old_view
        )


class JourneyRefreshButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"journey:refresh:(?P<session_id>[A-Za-z0-9_-]+)",
):
    """Pulls a session's story from storage, on demand.

    The message is not rewritten on every step — the Discord edit bucket is per
    channel and every session shares one. This button is how you see the middle
    of a story before it ends, and it also repairs a message a restart left
    stuck, because it renders from the events rather than from memory.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(
            discord.ui.Button(
                label="Refresh",
                emoji="🔄",
                style=discord.ButtonStyle.grey,
                custom_id=f"journey:refresh:{session_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["session_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        from app.logger import build_trace_embed
        from app.services import journey

        story = journey.rebuild(self.session_id)
        if not story:
            return await interaction.response.defer()

        await interaction.response.edit_message(
            embed=build_trace_embed(story), view=self.view
        )


def journey_message_view(session_id: str) -> discord.ui.View:
    """The refresh control that rides along with a session's log message."""
    view = discord.ui.View(timeout=None)
    view.add_item(JourneyRefreshButton(session_id))
    return view


async def run_feature_command(
    interaction: discord.Interaction, command_key: str, source: str
) -> None:
    """The single entry point from any button into a feature's configuration.

    Opens the trace the whole invocation is logged under, records where the
    user came from, and hands over to the form platform.
    """
    from app.services import analytics
    from app.services.trace import trace_scope

    analytics.mark_source(interaction, source)
    command = get_command_by_key(interaction.client, command_key)
    command_name = command.qualified_name if command else command_key

    async with trace_scope(
        command_name,
        opening=f"`/{command_name}` started",
        guild_id=interaction.guild_id,
        user_id=interaction.user.id,
        source=source,
        feature=command_key,
    ) as trace:
        trace.footnote = analytics.describe_attempt(
            analytics.count_attempt(interaction.guild_id, command_key), command_key
        )
        analytics.emit("command.invoked", command=command_name)
        increment_redis_key(f"{logconstants.COMMAND_CALL_TYPE}:{command_key}:button")

        from app.forms.adapters.discord.entrypoints import open_feature

        await open_feature(interaction, command_key, source)


class ExecuteCommandButton(discord.ui.Button):
    def __init__(self, command_key: str, locale: str) -> None:
        self.command_key = command_key
        label = ml("buttons.execute.label", locale=locale)
        super().__init__(
            label=label,
            emoji="▶️",
            style=discord.ButtonStyle.green,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        if not interaction.user.guild_permissions.administrator:
            embed = response_embed("buttons.setup.admin-only", parse_locale(interaction.locale))
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await run_feature_command(interaction, self.command_key, "help_button")


class HistoryButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.history.desc", locale=locale)
        super().__init__(
            label=ml("buttons.history.label", locale=locale),
            emoji="📜",
            style=discord.ButtonStyle.grey,
        )

class GenericButton(discord.ui.Button):
    def __init__(self, label: str, callback: Callable, style: discord.ButtonStyle, **kwargs):
        self.custom_callback = callback
        super().__init__(label=label, style=style, **kwargs)

    async def callback(self, interaction: discord.Interaction) -> Any:
        await self.custom_callback(interaction)

class SyncronizeRemindersButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Syncronize Reminders",
            emoji="🔄",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from app import bot

        await interaction.response.defer(thinking=True)

        reminders_deleted = []
        for reminder, count in self.view.guilds_by_reminder.items():
            if count > 0:
                continue

            bot.reminder.delete_reminder(reminder)
            reminders_deleted.append(self.view.streamers_by_reminder[reminder])

        return await interaction.followup.send(
            content=f"Deleting {', '.join(reminders_deleted)} reminders. Total of {len(reminders_deleted)} reminders deleted.",
        )

class RemoveDuplicatedReminderButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Remove Duplicated Reminders",
            emoji="🗑️",
            style=discord.ButtonStyle.red,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from app import bot

        await interaction.response.defer(thinking=True)

        removed_reminders = 0
        removed_by_streamers = {}
        for streamer, count in self.view.reminders_count_by_streamers.items():
            if count == 1:
                continue

            for reminder, streamer_name in self.view.streamers_by_reminder.items():
                if streamer_name != streamer:
                    continue

                bot.reminder.delete_reminder(reminder)

                removed_by_streamers[streamer] = removed_by_streamers.get(streamer, 0) + 1
                removed_reminders += 1

                if removed_by_streamers[streamer] == count - 1:
                    break

        message = "\n".join([f"Deleted {count} reminders from {streamer}" for streamer, count in removed_by_streamers.items()])

        return await interaction.followup.send(
            content=f"{message}\nTotal of {removed_reminders} reminders deleted",
        )


class SyncronizeSubscriptionsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Syncronize Subscriptions",
            emoji="🔄",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from app.services.notifications_twitch import handle_unsubscribe_streamer

        await interaction.response.defer(thinking=True)

        unsubscribed_streamers = []
        for streamer, count in self.view.guilds_by_streamer.items():
            if count > 0:
                continue

            handle_unsubscribe_streamer(interaction, {"streamer": {"value": streamer}})
            unsubscribed_streamers.append(streamer)

        return await interaction.followup.send(
            content=f"Unsubscribed from {', '.join(unsubscribed_streamers)}. Total of {len(unsubscribed_streamers)} streamers.",
        )

class PaginationButton(discord.ui.Button):
    def __init__(self, label: str, **kwargs):
        super().__init__(label=label, **kwargs)

    async def callback(self, interaction: discord.Interaction):
        current_page = self.view.current_page
        max_pages = self.view.max_pages

        if self.custom_id == 'prev_page':
            if current_page > 0:
                self.view.current_page -= 1
        elif self.custom_id == 'next_page':
            if current_page < max_pages - 1:
                self.view.current_page += 1

        self.view.update_buttons()
        await interaction.response.edit_message(view=self.view)
