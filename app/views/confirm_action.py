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


class DiscardChangesView(discord.ui.View):
    def __init__(
        self,
        source_interaction: discord.Interaction,
        source_view: discord.ui.View,
        locale: str,
    ) -> None:
        super().__init__(timeout=300)
        self.source_interaction = source_interaction
        self.source_view = source_view
        self.locale = locale
        self.keep.label = ml("buttons.cancel.keep", locale=locale)
        self.discard.label = ml("buttons.cancel.discard", locale=locale)

    @discord.ui.button(style=discord.ButtonStyle.green)
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        finalize_configuration_view(self.source_view)
        self.source_view.stop()
        try:
            await self.source_interaction.edit_original_response(view=self.source_view)
        except discord.NotFound:
            pass
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()


def finalize_configuration_view(view: discord.ui.View) -> None:
    if isinstance(view, discord.ui.LayoutView):
        def remove_action_rows(parent) -> None:
            for child in list(getattr(parent, "children", [])):
                if isinstance(child, discord.ui.ActionRow):
                    parent.remove_item(child)
                elif getattr(child, "children", None):
                    remove_action_rows(child)

        remove_action_rows(view)
        return
    view.clear_items()


async def request_discard_confirmation(
    interaction: discord.Interaction,
    source_view: discord.ui.View,
) -> None:
    from app.components.embed import response_error_embed

    embed = response_error_embed(
        "discard-settings-confirmation",
        source_view.locale,
        footer=False,
    )
    view = DiscardChangesView(
        source_interaction=interaction,
        source_view=source_view,
        locale=source_view.locale,
    )
    await interaction.response.defer()
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
