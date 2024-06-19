from typing import Any, Callable

import discord

from app.constants import KeikoIcons as icons
from app.services.utils import ml


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
        self.view.clear_items()
        await interaction.response.edit_message(view=self.view)


class OptionsButton(discord.ui.Button):
    def __init__(
        self,
        options_label: str,
        options_custom_id: str,
        unique: bool = False,
    ) -> None:
        self.unique = unique
        super().__init__(
            label=options_label,
            style=discord.ButtonStyle.gray,
            custom_id=options_custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.unique:
            self.handle_unique()

        if self.style == discord.ButtonStyle.primary:
            self.style = discord.ButtonStyle.gray
            del self.view.response[self.custom_id]
        else:
            self.style = discord.ButtonStyle.primary
            self.view.response[self.custom_id] = self.label

        await interaction.response.edit_message(view=self.view)

    def handle_unique(self) -> None:
        for item in self.view.children[:-2]:
            if not isinstance(item, discord.ui.Button):
                continue

            if item.custom_id == self.custom_id:
                continue

            if item.style == discord.ButtonStyle.primary:
                item.style = discord.ButtonStyle.gray
                self.view.response.pop(item.custom_id, None)
                return


class EditButtom(discord.ui.Button):
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

        self.view.clear_items()

        view = EditCommand(self.view.command_key, self.locale, self.after_callback)
        self.view.edited_form_view = view.form_view
        embed = interaction.message.embeds[0]

        await interaction.response.edit_message(embed=embed, view=view)


class PauseButtom(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.pause.desc", locale=locale)
        super().__init__(
            label=ml("buttons.pause.label", locale=locale),
            emoji="⏸️",
            custom_id=ml("commands.command-events.paused.action", locale=locale),
            style=discord.ButtonStyle.grey,
        )


class UnpauseButtom(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.unpause.desc", locale=locale)
        super().__init__(
            label=ml("buttons.unpause.label", locale=locale),
            emoji="▶️",
            custom_id=ml("commands.command-events.unpaused.action", locale=locale),
            style=discord.ButtonStyle.grey,
        )


class DisableButtom(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.disable.desc", locale=locale)
        super().__init__(
            label=ml("buttons.disable.label", locale=locale),
            emoji="🚫",
            custom_id=ml("commands.command-events.disabled.action", locale=locale),
            style=discord.ButtonStyle.grey,
        )


class BackButtom(discord.ui.Button):
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


class HistoryButtom(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.history.desc", locale=locale)
        super().__init__(
            label=ml("buttons.history.label", locale=locale),
            emoji="📜",
            style=discord.ButtonStyle.grey,
        )


class HelpButtom(discord.ui.Button):
    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.desc = ml("buttons.help.desc", locale=locale)
        super().__init__(
            label=ml("buttons.help.label", locale=locale),
            emoji="🙋",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        self.view.remove_item(self)

        embed = discord.Embed(
            title=f"🙋 {ml('buttons.help.label', self.locale)}",
            description=ml("buttons.captions.desc", self.locale),
        )
        embed.set_thumbnail(url=icons.IMAGE_02)

        for item in self.view.children:
            if not isinstance(item, discord.ui.Button):
                continue

            embed.add_field(
                name=f"{item.emoji} {item.label}",
                value=item.desc,
                inline=False,
            )

        await interaction.response.edit_message(
            embed=interaction.message.embeds[0], view=self.view
        )

        self.view.clear_items()

        await interaction.followup.send(embed=embed, view=self.view, ephemeral=True)


class AdditionalButton(discord.ui.Button):
    def __init__(self, callback: Callable, desc: str, **kwargs):
        self.custom_callback = callback
        self.desc = desc
        self.auto_disable = kwargs.pop("auto_disable", False)
        self.defer = kwargs.pop("defer", False)
        super().__init__(**kwargs)

    async def callback(self, interaction: discord.Interaction) -> Any:
        if self.auto_disable:
            self.view.remove_item(self)

        if self.defer:
            await interaction.response.defer()
            await interaction.edit_original_response(view=self.view)
        else:
            await interaction.response.edit_message(view=self.view)

        await self.custom_callback(interaction)
