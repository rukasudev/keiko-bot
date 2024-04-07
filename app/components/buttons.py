from typing import Any, Callable

import discord

from app.services.utils import ml


class ConfirmButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        super().__init__(
            label=ml("buttons.confirm", locale=locale), style=discord.ButtonStyle.green
        )


class CancelButton(discord.ui.Button):
    def __init__(self, locale: str) -> None:
        super().__init__(
            label=ml("buttons.cancel", locale=locale), style=discord.ButtonStyle.red
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.clear_items()
        await interaction.response.edit_message(view=self.view)


class OptionsButton(discord.ui.Button):
    def __init__(self, options_label: str, options_custom_id: str) -> None:
        super().__init__(
            label=options_label,
            style=discord.ButtonStyle.gray,
            custom_id=options_custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.style == discord.ButtonStyle.primary:
            self.style = discord.ButtonStyle.gray
            del self.view.response[self.custom_id]
        else:
            self.style = discord.ButtonStyle.primary
            self.view.response[self.custom_id] = self.label

        await interaction.response.edit_message(view=self.view)


class EditButtom(discord.ui.Button):
    def __init__(self, after_callback: Callable, locale: str) -> None:
        self.after_callback = after_callback
        self.locale = locale
        super().__init__(
            label=ml("buttons.edit", locale=locale),
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


class DisableButtom(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        super().__init__(
            label=ml("buttons.disable", locale=locale),
            emoji="🚫",
            style=discord.ButtonStyle.grey,
        )


class BackButtom(discord.ui.Button):
    def __init__(
        self, view: discord.ui.View, embed: discord.Embed, locale: str
    ) -> None:
        self.old_view = view
        self.old_embed = embed
        super().__init__(
            label=ml("buttons.back", locale=locale),
            emoji="🔙",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(
            embed=self.old_embed, view=self.old_view
        )


class HelpButtom(discord.ui.Button):
    def __init__(self, embed: discord.Embed, locale: str) -> None:
        self.embed = embed
        self.locale = locale
        super().__init__(
            label=ml("buttons.help", locale=locale),
            emoji="🙋",
            style=discord.ButtonStyle.grey,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        self.view.remove_item(self)

        await interaction.response.edit_message(
            embed=interaction.message.embeds[0], view=self.view
        )

        self.view.clear_items()

        await interaction.followup.send(
            embed=self.embed, view=self.view, ephemeral=True
        )


class AdditionalButton(discord.ui.Button):
    def __init__(self, callback: Callable, desc: str, **kwargs):
        self.callback = callback
        self.desc = desc
        super().__init__(**kwargs)
