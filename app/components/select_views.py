from typing import Callable, Dict

import discord

from app.components.buttons import CancelButton
from app.services.utils import ml


class SelectConfirmButton(discord.ui.Button):
    """Confirm button that validates selection before proceeding."""

    def __init__(self, callback: Callable, locale: str, required: bool = False):
        self.custom_callback = callback
        self.required = required
        self.locale = locale
        super().__init__(
            label=ml("buttons.confirm.label", locale=locale),
            style=discord.ButtonStyle.green,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.required and not self.view.response:
            error_msg = ml("errors.selection-required.message", locale=self.locale)
            await interaction.response.send_message(
                content=error_msg or "Please select an option before confirming.",
                ephemeral=True,
                delete_after=5
            )
            return
        await self.custom_callback(interaction)


class ChannelSelectView(discord.ui.View):
    """Native Discord ChannelSelect for channel selection."""

    def __init__(self, callback: Callable, locale: str, required: bool = False, unique: bool = True):
        super().__init__(timeout=1800)
        self.callback = callback
        self.locale = locale
        self.required = required
        self.unique = unique
        self.response: Dict[str, str] = {}

        placeholder = ml("buttons.components.select.channel-placeholder", locale=locale)

        self.channel_select = discord.ui.ChannelSelect(
            placeholder=placeholder or "Select a channel...",
            min_values=0,
            max_values=1 if unique else 25,
            channel_types=[discord.ChannelType.text],
        )
        self.channel_select.callback = self._on_select
        self.add_item(self.channel_select)
        self.add_item(SelectConfirmButton(callback=callback, locale=locale, required=required))
        self.add_item(CancelButton(locale=locale))

    async def _on_select(self, interaction: discord.Interaction):
        self.response = {str(ch.id): ch.name for ch in self.channel_select.values}
        await interaction.response.defer()

    def get_response(self):
        keys = list(self.response.keys())
        return keys[0] if len(keys) == 1 else keys


class RoleSelectView(discord.ui.View):
    """Native Discord RoleSelect for role selection."""

    def __init__(self, callback: Callable, locale: str, required: bool = False, unique: bool = True):
        super().__init__(timeout=1800)
        self.callback = callback
        self.locale = locale
        self.required = required
        self.unique = unique
        self.response: Dict[str, str] = {}

        placeholder = ml("buttons.components.select.role-placeholder", locale=locale)

        self.role_select = discord.ui.RoleSelect(
            placeholder=placeholder or "Select a role...",
            min_values=0,
            max_values=1 if unique else 25,
        )
        self.role_select.callback = self._on_select
        self.add_item(self.role_select)
        self.add_item(SelectConfirmButton(callback=callback, locale=locale, required=required))
        self.add_item(CancelButton(locale=locale))

    async def _on_select(self, interaction: discord.Interaction):
        self.response = {str(role.id): role.name for role in self.role_select.values}
        await interaction.response.defer()

    def get_response(self):
        keys = list(self.response.keys())
        return keys[0] if len(keys) == 1 else keys


class MultiSelectView(discord.ui.View):
    """View with multiple native selects (channels and/or roles)."""

    def __init__(self, config: dict, callback: Callable, locale: str):
        super().__init__(timeout=1800)
        self.callback = callback
        self.locale = locale
        self.responses: Dict[str, Dict[str, str]] = {}
        self.select_styles: Dict[str, str] = {}

        for select_config in config.get("selects", []):
            select_type = select_config.get("type")
            key = select_config.get("key")
            style = select_config.get("style")
            placeholder = select_config.get("placeholder", {}).get(locale, "Select...")

            self.responses[key] = {}

            if select_type == "channels":
                select = discord.ui.ChannelSelect(
                    placeholder=placeholder,
                    channel_types=[discord.ChannelType.text],
                    max_values=25,
                )
                self.select_styles[key] = style or "channel"
            elif select_type in ("roles", "available_roles"):
                select = discord.ui.RoleSelect(
                    placeholder=placeholder,
                    max_values=25,
                )
                self.select_styles[key] = style or "role"
            else:
                continue

            select.callback = self._make_callback(key, select_type, select)
            self.add_item(select)

        self.add_item(SelectConfirmButton(callback=callback, locale=locale, required=False))
        self.add_item(CancelButton(locale=locale))

    def _make_callback(self, key: str, select_type: str, select_component):
        async def callback(interaction: discord.Interaction):
            if select_type == "channels":
                self.responses[key] = {str(ch.id): ch.name for ch in select_component.values}
            else:
                self.responses[key] = {str(r.id): r.name for r in select_component.values}
            await interaction.response.defer()
        return callback

    def get_response(self):
        result = {}
        for key, values in self.responses.items():
            if values:
                result[key] = {
                    "values": list(values.keys())[0] if len(values) == 1 else list(values.keys()),
                    "style": self.select_styles.get(key)
                }
        return result
