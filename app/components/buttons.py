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
        checked: bool = False,
    ) -> None:
        self.unique = unique
        super().__init__(
            label=options_label,
            style=discord.ButtonStyle.gray if not checked else discord.ButtonStyle.primary,
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
                item.style = discord.ButtonStyle.gray

        self.view.response = {self.custom_id: self.label}


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

        self.view.clear_items()

        view = EditCommand(self.view.command_key, self.view.cogs or self.view._parse_responses_to_cog(), self.locale, self.after_callback)
        self.view.edited_form_view = view.form_view
        embed = interaction.message.embeds[0]

        await interaction.response.edit_message(embed=embed, view=view)


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
        self.desc = ml("buttons.preview.desc", locale=locale)
        super().__init__(
            label=ml("buttons.preview.label", locale=locale),
            emoji="👁️",
            style=discord.ButtonStyle.gray,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        self.view.remove_item(self)

        await interaction.response.edit_message(view=self.view)

        if not hasattr(self.view, "responses"):
            responses = []
        else:
            responses = self.view.responses

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
        from app.views.remove import RemoveItem

        self.view.clear_items()

        view = RemoveItem(self.view.command_key, self.view.cogs, self.locale, self.after_callback)
        embed = interaction.message.embeds[0]

        await interaction.response.edit_message(embed=embed, view=view)

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

        self.view.clear_items()

        view = Form(self.view.command_key, self.locale, cogs=self.view.cogs)
        view.filter_steps(constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.view.command_key])
        view._set_after_callback(self.after_callback)

        self.view.form_view = view

        await view._callback(interaction)


class HelpButton(discord.ui.Button):
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

class GenericButton(discord.ui.Button):
    def __init__(self, label: str, callback: Callable, style: discord.ButtonStyle, **kwargs):
        self.custom_callback = callback
        super().__init__(label=label, style=style, **kwargs)

    async def callback(self, interaction: discord.Interaction) -> Any:
        await self.custom_callback(interaction)

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
