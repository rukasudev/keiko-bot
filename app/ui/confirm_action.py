from typing import Any, Awaitable, Callable, Optional, Sequence

import discord

from app.constants import ViewConstants as view_constants
from app.core.trace import trace_scope
from app.core.utils import is_guild_admin, ml, speak


class ConfirmActionView(discord.ui.View):
    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        locale: str,
        on_cancel: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
        confirm_label_key: str = "buttons.confirm.label",
        cancel_label_key: str = "buttons.cancel.label",
        trace_name: Optional[str] = None,
        more: Sequence[discord.ui.Item[Any]] = (),
    ) -> None:
        super().__init__(timeout=view_constants.SHORT_TIMEOUT_SECONDS)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._trace_name = trace_name
        self.locale = locale
        self.confirm.label = ml(confirm_label_key, locale=locale)
        self.cancel.label = ml(cancel_label_key, locale=locale)
        if more:
            self.remove_item(self.cancel)
            for item in more:
                self.add_item(item)
            self.add_item(self.cancel)

    async def close(
        self, interaction: discord.Interaction, notice: Optional[discord.Embed] = None
    ) -> None:
        """Take the buttons off the question, its embed replaced by `notice` if given."""
        if notice is not None:
            await interaction.response.edit_message(embed=notice, view=None)
            return
        await interaction.response.edit_message(view=None)

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        speak(interaction, self.locale)
        if self._trace_name:
            async with trace_scope(
                self._trace_name,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                source="slash",
                is_admin=is_guild_admin(interaction.user),
            ):
                await self._on_confirm(interaction)
        else:
            await self._on_confirm(interaction)
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        speak(interaction, self.locale)
        if self._on_cancel:
            await self._on_cancel(interaction)
        else:
            await self.close(interaction)
        self.stop()


class ConfirmActionCard(discord.ui.LayoutView):
    """A question drawn as a Components V2 card, with Confirm, the `more` items and
    Cancel in one row below it, that keeps its card once answered."""

    def __init__(
        self,
        card: discord.ui.Container[Any],
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        locale: str,
        more: Sequence[discord.ui.Item[Any]] = (),
    ) -> None:
        super().__init__(timeout=view_constants.SHORT_TIMEOUT_SECONDS)
        self._on_confirm = on_confirm
        self.locale = locale
        confirm: discord.ui.Button[Any] = discord.ui.Button(
            label=ml("buttons.confirm.label", locale=locale),
            style=discord.ButtonStyle.green,
        )
        cancel: discord.ui.Button[Any] = discord.ui.Button(
            label=ml("buttons.cancel.label", locale=locale),
            style=discord.ButtonStyle.red,
        )
        confirm.callback = self._confirmed
        cancel.callback = self._cancelled
        self.buttons: discord.ui.ActionRow[Any] = discord.ui.ActionRow(
            confirm, *more, cancel
        )
        self.add_item(card)
        self.add_item(self.buttons)

    async def close(
        self, interaction: discord.Interaction, notice: Optional[discord.Embed] = None
    ) -> None:
        """Draw the card again without its buttons, and `notice` beside it if given."""
        self.remove_item(self.buttons)
        await interaction.response.edit_message(view=self)
        if notice is not None:
            await interaction.followup.send(embed=notice, ephemeral=True)

    async def _confirmed(self, interaction: discord.Interaction) -> None:
        speak(interaction, self.locale)
        await self._on_confirm(interaction)
        self.stop()

    async def _cancelled(self, interaction: discord.Interaction) -> None:
        speak(interaction, self.locale)
        await self.close(interaction)
        self.stop()
