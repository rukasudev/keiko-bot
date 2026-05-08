from typing import Awaitable, Callable, Optional

import discord

from app.services.utils import ml


class ConfirmActionView(discord.ui.View):
    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        locale: str,
        on_cancel: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(timeout=300)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self.locale = locale
        self.confirm.label = ml("buttons.confirm.label", locale=locale)
        self.cancel.label = ml("buttons.cancel.label", locale=locale)

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._on_confirm(interaction)
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self._on_cancel:
            await self._on_cancel(interaction)
        else:
            await interaction.response.edit_message(view=None)
        self.stop()
