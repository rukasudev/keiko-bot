import importlib
import time
from typing import Any, Callable, Optional

import discord

from app import logger
from app.components.embed import response_embed
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons as icons
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.services.cache import increment_redis_key
from app.services.utils import get_command_by_key, ml, parse_locale


READY, NOTIFY, SILENT = "ready", "notify", "silent"


class ActionCooldown:
    """Anti-spam window for buttons that answer with a message of their own.
    At most one notice is visible at a time; navigation buttons never get
    one (double-clicking them must keep working)."""

    def __init__(self, seconds: float = view_constants.ACTION_COOLDOWN_SECONDS):
        self.seconds = seconds
        self._last_use: Optional[float] = None
        self._notified_at: Optional[float] = None

    def poll(self) -> str:
        now = time.monotonic()
        if self._last_use is None or now - self._last_use >= self.seconds:
            self._last_use = now
            self._notified_at = None
            return READY
        notice_expired = (
            self._notified_at is None
            or now - self._notified_at >= view_constants.ACTION_NOTICE_SECONDS
        )
        if notice_expired:
            self._notified_at = now
            return NOTIFY
        return SILENT


async def acknowledge_hot_click(interaction: discord.Interaction, locale: str,
                                state: str) -> None:
    if state == SILENT:
        if not interaction.response.is_done():
            await interaction.response.defer()
        return

    embed = response_embed("buttons.cooldown", locale)
    if interaction.response.is_done():
        return await interaction.followup.send(embed=embed, ephemeral=True)
    await interaction.response.send_message(
        embed=embed, ephemeral=True,
        delete_after=view_constants.ACTION_NOTICE_SECONDS,
    )


class ConfirmButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.confirm.desc", locale=locale)
        super().__init__(
            label=ml("buttons.confirm.label", locale=locale),
            style=discord.ButtonStyle.green,
        )


class CancelButton(discord.ui.Button):
    def __init__(self, locale: str) -> None:
        self.desc = ml("buttons.cancel.desc", locale=locale)
        super().__init__(
            label=ml("buttons.cancel.label", locale=locale),
            style=discord.ButtonStyle.red,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from app.views.confirm_action import request_discard_confirmation

        await request_discard_confirmation(interaction, self.view)


def keep_cancel_button_last(view: discord.ui.View) -> None:
    """Keiko UI convention: the red Cancel button is always the last button in
    a view. Buttons render in add-order, so callers that append items after the
    view is built (like the form back button) must re-anchor Cancel at the end."""
    for item in [child for child in view.children if isinstance(child, CancelButton)]:
        view.remove_item(item)
        view.add_item(item)


OPTION_BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def resolve_option_style(
    option: Any, default: discord.ButtonStyle = discord.ButtonStyle.gray
) -> discord.ButtonStyle:
    """YAML `options[].style` -> ButtonStyle, for every view that renders an
    option as a button (form options steps and card option pickers alike).
    Unknown or missing styles keep the neutral default."""
    if not isinstance(option, dict):
        return default
    return OPTION_BUTTON_STYLES.get(option.get("style"), default)


class OptionsButton(discord.ui.Button):
    def __init__(
        self,
        options_label: str,
        options_custom_id: str,
        unique: bool = False,
        checked: bool = False,
        auto_confirm: bool = False,
        default_style: discord.ButtonStyle = discord.ButtonStyle.gray,
    ) -> None:
        self.unique = unique
        self.auto_confirm = auto_confirm
        self.default_style = default_style
        super().__init__(
            label=options_label,
            style=default_style if not checked else discord.ButtonStyle.primary,
            custom_id=options_custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.unique:
            self.handle_unique()

        if self.style == discord.ButtonStyle.primary:
            self.style = self.default_style
            del self.view.response[self.custom_id]
        else:
            self.style = discord.ButtonStyle.primary
            self.view.response[self.custom_id] = self.label

        if self.auto_confirm:
            return await self.view.callback(interaction)

        embed = self.get_embed_values(interaction)

        await interaction.response.edit_message(view=self.view, embed=embed)

    def get_embed_values(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]

        embed.clear_fields()

        selected = ml("buttons.selected.label", locale=self.view.locale)
        for index, response in enumerate(self.view.response.values()):
            embed.add_field(
                name=f":flying_disc: {selected} #{index + 1}",
                value=response,
                inline=False,
            )

        return embed

    def handle_unique(self) -> None:
        for item in self.view.children[:-2]:
            if not isinstance(item, discord.ui.Button):
                continue

            if item.custom_id == self.custom_id:
                continue

            if item.custom_id == "prev_page" or item.custom_id == "next_page":
                continue

            if item.style == discord.ButtonStyle.primary:
                item.style = item.default_style

        self.view.response = {self.custom_id: self.label}


def panel_screen_embed(interaction: discord.Interaction, command_key: str,
                       locale: str) -> discord.Embed:
    """The embed a screen opened from the manager panel starts from."""
    from app.components.embed import parse_form_dict_to_embed
    from app.services.utils import parse_form_yaml_to_dict

    if interaction.message and interaction.message.embeds:
        return interaction.message.embeds[0]

    first_step = list(parse_form_yaml_to_dict(command_key))[0]
    return parse_form_dict_to_embed(first_step, locale, True)


class EditButton(discord.ui.Button):
    def __init__(self, after_callback: Callable, locale: str) -> None:
        self.after_callback = after_callback
        self.locale = locale
        self.desc = ml("buttons.edit.desc", locale=locale)
        super().__init__(
            label=ml("buttons.edit.label", locale=locale),
            emoji="📝",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction):
        from app.views.edit import EditCommand
        from app.views.panel_transitions import transition_to_embed

        parent_view = self.view
        view = EditCommand(parent_view.command_key, parent_view.cogs or parent_view._parse_responses_to_cog(), self.locale, self.after_callback, parent_view=parent_view)
        parent_view.edited_form_view = view.form_view
        embed = panel_screen_embed(interaction, parent_view.command_key, self.locale)
        parent_view._original_embed = embed

        await transition_to_embed(interaction, embed, view)


class PauseButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.pause.desc", locale=locale)
        super().__init__(
            label=ml("buttons.pause.label", locale=locale),
            emoji="⏸️",
            custom_id=ml("commands.command-events.paused.action", locale=locale),
            style=discord.ButtonStyle.grey,
        )

class PreviewButton(discord.ui.Button):
    def __init__(self, custom_callback: Callable, locale: str, command_key: str) -> None:
        self.custom_callback = custom_callback
        self.command_key = command_key
        self.locale = locale
        self.desc = ml("buttons.preview.desc", locale=locale)
        self.cooldown = ActionCooldown()
        super().__init__(
            label=ml("buttons.preview.label", locale=locale),
            emoji="👁️",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        state = self.cooldown.poll()
        if state != READY:
            return await acknowledge_hot_click(interaction, self.locale, state)

        await interaction.response.defer()

        view = self.view
        responses = view.responses if hasattr(view, "responses") else []

        from app.services import analytics
        analytics.emit(
            "feature.tested",
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            feature=self.command_key,
            surface="preview",
        )

        await self.custom_callback(interaction, responses)


class UnpauseButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.unpause.desc", locale=locale)
        super().__init__(
            label=ml("buttons.unpause.label", locale=locale),
            emoji="▶️",
            custom_id=ml("commands.command-events.unpaused.action", locale=locale),
            style=discord.ButtonStyle.grey,
        )


class DisableButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.disable.desc", locale=locale)
        super().__init__(
            label=ml("buttons.disable.label", locale=locale),
            emoji="🚫",
            custom_id=ml("commands.command-events.disabled.action", locale=locale),
            style=discord.ButtonStyle.grey,
        )


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
    user came from, and hands over to the feature service.
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

        service = importlib.import_module(commands_constants.COMMAND_SERVICES[command_key])
        await service.manager(
            interaction=interaction, guild_id=str(interaction.guild.id)
        )


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


class FormBackButton(discord.ui.Button):
    """Back button for form flows that allows navigating to the previous step."""

    def __init__(self, form_view, locale: str) -> None:
        self.form_view = form_view
        self.desc = ml("buttons.back.desc", locale=locale)
        super().__init__(
            label=ml("buttons.back.label", locale=locale),
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        await self.form_view._go_back(interaction)


class HistoryButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.history.desc", locale=locale)
        super().__init__(
            label=ml("buttons.history.label", locale=locale),
            emoji="📜",
            style=discord.ButtonStyle.grey,
        )

class RemoveItemButton(discord.ui.Button):
    def __init__(self, after_callback: Callable, locale: str) -> None:
        self.after_callback = after_callback
        self.desc = ml("buttons.remove.desc", locale=locale)
        self.locale = locale
        super().__init__(
            label=ml("buttons.remove.label", locale=locale),
            emoji="🗑️",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction):
        from app.views.panel_transitions import transition_to_embed
        from app.views.remove import RemoveItem

        parent_view = self.view
        view = RemoveItem(parent_view.command_key, parent_view.cogs or parent_view._parse_responses_to_cog(), self.locale, self.after_callback)
        embed = panel_screen_embed(interaction, parent_view.command_key, self.locale)

        await transition_to_embed(interaction, embed, view)

class AddItemButton(discord.ui.Button):
    def __init__(self, after_callback: Callable, locale: str) -> None:
        self.after_callback = after_callback
        self.desc = ml("buttons.add.desc", locale=locale)
        self.locale = locale
        super().__init__(
            label=ml("buttons.add.label", locale=locale),
            emoji="➕",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction):
        from app.constants import Commands as constants
        from app.views.form import Form

        parent_view = self.view
        view = Form(parent_view.command_key, self.locale, cogs=parent_view.cogs or parent_view._parse_responses_to_cog())
        view.inherit_context(parent_view)
        view.filter_steps(constants.COMMAND_KEY_TO_COMPOSITION_KEY[parent_view.command_key])
        view._set_after_callback(self.after_callback)

        parent_view.form_view = view

        await view._callback(interaction)


class HelpButton(discord.ui.Button):
    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.desc = ml("buttons.help.desc", locale=locale)
        self.cooldown = ActionCooldown()
        super().__init__(
            label=ml("buttons.help.label", locale=locale),
            emoji="🙋",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        state = self.cooldown.poll()
        if state != READY:
            return await acknowledge_hot_click(interaction, self.locale, state)
        """Read-only screen: the captions arrive as their own ephemeral
        message and the panel is left exactly as it was.

        Editing the panel here is what used to kill it: the old
        edit-then-clear_items dance left the on-screen buttons pointing at a
        cleared view, and on a Components V2 panel the edit itself crashed
        (the message has no embeds). Help must never cost the user the panel.
        """
        from app.constants import Style as style_constants

        embed = discord.Embed(
            title=f"🙋 {ml('buttons.help.label', self.locale)}",
            description=ml("buttons.captions.desc", self.locale),
            color=int(style_constants.BACKGROUND_COLOR, base=16),
        )
        embed.set_thumbnail(url=icons.IMAGE_02)

        for item in self.view.walk_children():
            if not isinstance(item, discord.ui.Button) or item is self:
                continue
            if not item.label or not getattr(item, "desc", None):
                continue
            embed.add_field(
                name=f"{item.emoji} {item.label}" if item.emoji else item.label,
                value=item.desc,
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdditionalButton(discord.ui.Button):
    def __init__(self, callback: Callable, desc: str, **kwargs):
        self.custom_callback = callback
        self.desc = desc
        self.defer = kwargs.pop("defer", False)
        self.own_response = kwargs.pop("own_response", False)
        cooldown = kwargs.pop("cooldown", None)
        self.cooldown = ActionCooldown(cooldown) if cooldown else None
        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction) -> Any:
        if self.cooldown:
            state = self.cooldown.poll()
            if state != READY:
                locale = parse_locale(interaction.locale)
                return await acknowledge_hot_click(interaction, locale, state)

        if self.own_response:
            return await self.custom_callback(interaction)

        if self.defer:
            await interaction.response.defer()

        await self.custom_callback(interaction)

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
