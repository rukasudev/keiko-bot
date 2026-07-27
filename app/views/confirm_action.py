from typing import Awaitable, Callable, Optional

import discord

from app.services.utils import ml


class ConfirmActionView(discord.ui.View):
    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        locale: str,
        on_cancel: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
        confirm_label_key: str = "buttons.confirm.label",
        cancel_label_key: str = "buttons.cancel.label",
    ) -> None:
        super().__init__(timeout=300)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self.locale = locale
        self.confirm.label = ml(confirm_label_key, locale=locale)
        self.cancel.label = ml(cancel_label_key, locale=locale)

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

    async def keep(keep_interaction: discord.Interaction) -> None:
        await keep_interaction.response.defer()
        await keep_interaction.delete_original_response()

    async def discard(discard_interaction: discord.Interaction) -> None:
        finalize_configuration_view(source_view)
        source_view.stop()
        try:
            await interaction.edit_original_response(view=source_view)
        except discord.NotFound:
            pass
        await discard_interaction.response.defer()
        await discard_interaction.delete_original_response()

    embed = response_error_embed(
        "discard-settings-confirmation",
        source_view.locale,
        footer=False,
    )
    view = ConfirmActionView(
        on_confirm=keep,
        locale=source_view.locale,
        on_cancel=discard,
        confirm_label_key="buttons.cancel.keep",
        cancel_label_key="buttons.cancel.discard",
    )
    await interaction.response.defer()
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
