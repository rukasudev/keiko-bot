from io import BytesIO
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
        self.selects: Dict[str, discord.ui.Select] = {}

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
            self.selects[key] = select
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


class DesignSelectView(discord.ui.LayoutView):
    def __init__(self, callback: Callable, locale: str, designs: list, back_callback: Callable = None, preview_urls: dict = None):
        super().__init__(timeout=1800)
        self.custom_callback = callback
        self.locale = locale
        self.response: Dict[str, str] = {}
        self._designs = designs
        self._back_callback = back_callback
        self._preview_urls = preview_urls or {}
        self._selection_made = False

        container = discord.ui.Container(accent_colour=discord.Colour.blurple())

        header_text = ml("buttons.components.design-select.header", locale=locale) or "Select one of the 3 designs below:"
        container.add_item(discord.ui.TextDisplay(header_text))
        container.add_item(discord.ui.Separator())

        for i, design in enumerate(designs):
            label = design["label"].get(locale) or design["label"].get("en-us", "")
            desc = design["description"].get(locale) or design["description"].get("en-us", "")
            preview_url = self._preview_urls.get(design["key"]) or design.get("preview_url")

            select_label = ml("buttons.select.label", locale=locale) or "Select"
            btn = discord.ui.Button(
                label=select_label,
                custom_id=f"design_{design['key']}",
                style=discord.ButtonStyle.primary,
            )

            container.add_item(discord.ui.TextDisplay(f"**{label}**\n{desc}"))

            if preview_url:
                gallery = discord.ui.MediaGallery(discord.MediaGalleryItem(media=preview_url))
                container.add_item(gallery)

            container.add_item(discord.ui.ActionRow(btn))

            if i < len(designs) - 1:
                container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.Separator())
        footer_text = ml("buttons.components.design-select.footer", locale=locale) or "Scroll up to see all options"
        container.add_item(discord.ui.TextDisplay(footer_text))
        container.add_item(discord.ui.Separator())

        buttons = []
        cancel_btn = discord.ui.Button(
            label=ml("buttons.cancel.label", locale=locale) or "Cancel",
            style=discord.ButtonStyle.danger,
            custom_id="cancel_design"
        )
        buttons.append(cancel_btn)

        if back_callback:
            back_btn = discord.ui.Button(
                label=ml("buttons.back.label", locale=locale) or "Back",
                style=discord.ButtonStyle.secondary,
                custom_id="back_design"
            )
            buttons.append(back_btn)

        action_row = discord.ui.ActionRow(*buttons)
        container.add_item(action_row)

        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id", "")

        if custom_id == "cancel_design":
            await interaction.response.edit_message(
                content=ml("buttons.cancel.cancelled", locale=self.locale) or "Cancelled.",
                view=None
            )
            self.stop()
            return False

        if custom_id == "back_design" and self._back_callback:
            await self._back_callback(interaction)
            return False

        if custom_id.startswith("design_"):
            key = custom_id.replace("design_", "")
            design = next((d for d in self._designs if d["key"] == key), None)
            if design:
                label = design["label"].get(self.locale) or design["label"].get("en-us", "")
                self.response = {key: label}
                await self.custom_callback(interaction, reselection=self._selection_made)
                self._selection_made = True
                return False

        return True

    def get_response(self):
        return list(self.response.keys())[0] if self.response else None


class FileUploadModal(discord.ui.Modal):
    def __init__(self, callback: Callable, locale: str, title: str = None):
        super().__init__(title=title, timeout=300)
        self.custom_callback = callback
        self.locale = locale
        self._response = None

        label_text = ml("buttons.file-upload.label", locale=locale) or "Image"
        self.file_upload = discord.ui.FileUpload(custom_id="custom_image_upload")
        label = discord.ui.Label(text=label_text, component=self.file_upload)
        self.add_item(label)

    async def on_submit(self, interaction: discord.Interaction):
        if self.file_upload.values:
            attachment = self.file_upload.values[0]
            permanent_url = await self._upload_to_dump_channel(attachment)
            self._response = permanent_url
        await self.custom_callback(interaction)

    async def _upload_to_dump_channel(self, attachment: discord.Attachment) -> str:
        """Upload arquivo para dump_channel para obter URL permanente."""
        from app import bot

        file_bytes = await attachment.read()
        dump_channel = bot.get_channel(bot.config.ADMIN_DUMP_CHANNEL_ID)
        message = await dump_channel.send(
            file=discord.File(fp=BytesIO(file_bytes), filename=attachment.filename)
        )
        return message.attachments[0].url

    def get_response(self):
        return self._response
