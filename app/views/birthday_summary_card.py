from typing import Any, Callable, Dict, Optional

import discord

from app import logger
from app.components.select_views import FileUploadModal
from app.constants import KeikoIcons, Style
from app.constants import LogTypes as logconstants
from app.services.birthdays import birthday_default_text, format_birthday_date_value, render_birthday_message
from app.services.utils import ml


class CustomBirthdayMessageModal(discord.ui.Modal):
    """Modal with title and content for a custom birthday message."""

    def __init__(self, callback: Callable, locale: str, current_title: str = None, current_content: str = None) -> None:
        modal_title = ml("buttons.summary-card.modal-title-message", locale=locale) or "Customize Message"
        super().__init__(title=modal_title, timeout=300)
        self.custom_callback = callback
        self.locale = locale

        title_label = ml("buttons.summary-card.label-title", locale=locale) or "Title"
        content_label = ml("buttons.summary-card.label-content", locale=locale) or "Content"

        self.title_input = discord.ui.TextInput(
            label=title_label,
            style=discord.TextStyle.short,
            max_length=50,
            required=True,
            default=current_title or "",
        )
        self.content_input = discord.ui.TextInput(
            label=content_label,
            style=discord.TextStyle.long,
            max_length=200,
            required=True,
            default=current_content or "",
        )
        self.add_item(self.title_input)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.custom_callback(interaction, self.title_input.value, self.content_input.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(
            f"CustomBirthdayMessageModal error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


class BirthdaySummaryCardView(discord.ui.LayoutView):
    """LayoutView showing the reminder state with opt-in customization toggles."""

    def __init__(
        self,
        callback: Callable,
        locale: str,
        member_name: str,
        member_id: str,
        guild_name: str,
        mm_dd: str,
        prior_state: Optional[Dict[str, Any]] = None,
        back_callback: Optional[Callable] = None,
    ) -> None:
        super().__init__(timeout=1800)
        self.custom_callback = callback
        self.locale = locale
        self.member_name = member_name
        self.member_id = member_id
        self.guild_name = guild_name
        self.mm_dd = mm_dd
        self._back_callback = back_callback

        self.state: Dict[str, Any] = prior_state or {
            "use_custom_message": "default",
            "custom_message_title": None,
            "custom_message_content": None,
            "use_custom_image": "default",
            "custom_image": None,
        }

        self._render()

    def _render(self) -> None:
        for item in list(self.children):
            self.remove_item(item)

        container = discord.ui.Container(accent_colour=discord.Colour(int(Style.BACKGROUND_COLOR, 16)))

        title_text = ml("buttons.summary-card.title", locale=self.locale) or "Reminder Setup"
        member_label = ml("buttons.summary-card.member", locale=self.locale) or "Member"
        date_label = ml("buttons.summary-card.date", locale=self.locale) or "Birthday"
        date_text = format_birthday_date_value(self.mm_dd, self.locale)
        container.add_item(discord.ui.Section(
            f"## 🎂 {title_text}",
            f"👤 **{member_label}:** <@{self.member_id}>",
            f"📅 **{date_label}:** {date_text}",
            accessory=discord.ui.Thumbnail(KeikoIcons.BIRTHDAY_GIF),
        ))
        container.add_item(discord.ui.Separator())

        message_label = ml("buttons.summary-card.message", locale=self.locale) or "Message"
        if self.state["use_custom_message"] == "custom":
            badge = ml("buttons.summary-card.badge-custom", locale=self.locale) or "✨ Custom"
            title_preview = self.state.get("custom_message_title") or ""
            content_preview = self.state.get("custom_message_content") or ""
            container.add_item(discord.ui.TextDisplay(f"💬 **{message_label}:** {badge}"))
            self._add_message_preview(container, title_preview, content_preview)
            edit_label = ml("buttons.summary-card.edit", locale=self.locale) or "Edit"
            reset_label = ml("buttons.summary-card.reset", locale=self.locale) or "Reset to default"
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(label=edit_label, custom_id="card_edit_msg", style=discord.ButtonStyle.grey),
                discord.ui.Button(label=reset_label, custom_id="card_reset_msg", style=discord.ButtonStyle.grey),
            ))
        else:
            badge = ml("buttons.summary-card.badge-default", locale=self.locale) or "📌 Keiko's default"
            customize_label = ml("buttons.summary-card.customize-msg", locale=self.locale) or "Customize message"
            container.add_item(discord.ui.TextDisplay(f"💬 **{message_label}:** {badge}"))
            self._add_message_preview(
                container,
                birthday_default_text("title", self.locale),
                birthday_default_text("content", self.locale),
            )
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(label=customize_label, custom_id="card_customize_msg", style=discord.ButtonStyle.grey),
            ))

        container.add_item(discord.ui.Separator())

        image_label = ml("buttons.summary-card.image", locale=self.locale) or "Image"
        if self.state["use_custom_image"] == "custom":
            badge = ml("buttons.summary-card.badge-custom", locale=self.locale) or "✨ Custom"
            container.add_item(discord.ui.TextDisplay(f"🖼️ **{image_label}:** {badge}"))
            if self.state.get("custom_image"):
                container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media=self.state["custom_image"])))
            edit_label = ml("buttons.summary-card.edit", locale=self.locale) or "Edit"
            reset_label = ml("buttons.summary-card.reset", locale=self.locale) or "Reset to default"
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(label=edit_label, custom_id="card_edit_img", style=discord.ButtonStyle.grey),
                discord.ui.Button(label=reset_label, custom_id="card_reset_img", style=discord.ButtonStyle.grey),
            ))
        else:
            badge = ml("buttons.summary-card.badge-default", locale=self.locale) or "📌 Keiko's default"
            customize_label = ml("buttons.summary-card.customize-img", locale=self.locale) or "Customize image"
            container.add_item(discord.ui.TextDisplay(f"🖼️ **{image_label}:** {badge}"))
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(label=customize_label, custom_id="card_customize_img", style=discord.ButtonStyle.grey),
            ))

        container.add_item(discord.ui.Separator())

        action_buttons = []
        done_label = ml("buttons.summary-card.done", locale=self.locale) or "Done"
        action_buttons.append(discord.ui.Button(label=done_label, custom_id="card_done", style=discord.ButtonStyle.green))

        cancel_label = ml("buttons.cancel.label", locale=self.locale) or "Cancel"
        action_buttons.append(discord.ui.Button(label=cancel_label, custom_id="card_cancel", style=discord.ButtonStyle.red))

        if self._back_callback:
            back_label = ml("buttons.back.label", locale=self.locale) or "Back"
            action_buttons.append(discord.ui.Button(label=back_label, custom_id="card_back", style=discord.ButtonStyle.secondary))

        container.add_item(discord.ui.ActionRow(*action_buttons))
        self.add_item(container)

    def _add_message_preview(self, container: discord.ui.Container, title: str, content: str) -> None:
        title = self._format_preview_text(title)
        content = self._format_preview_text(content)
        lines = []
        if title:
            lines.append(f"> **{title}**")
        if content:
            lines.append(f"> {content}")
        if lines:
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))

    def _format_preview_text(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return render_birthday_message(
            text,
            f"<@{self.member_id}>",
            self.guild_name,
            self.mm_dd,
            self.locale,
        )

    def get_response(self) -> Dict[str, Any]:
        return self.state

    async def _on_msg_modal_submit(self, interaction: discord.Interaction, title: str, content: str) -> None:
        self.state["use_custom_message"] = "custom"
        self.state["custom_message_title"] = title
        self.state["custom_message_content"] = content
        self._render()
        await interaction.response.edit_message(view=self)

    async def _on_img_modal_submit(self, interaction: discord.Interaction) -> None:
        url = self._pending_file_modal.get_response() if hasattr(self, "_pending_file_modal") else None
        if url:
            self.state["use_custom_image"] = "custom"
            self.state["custom_image"] = url
        self._render()
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id", "")

        if custom_id == "card_cancel":
            await interaction.response.defer()
            await interaction.delete_original_response()
            self.stop()
            return False

        if custom_id == "card_back" and self._back_callback:
            await self._back_callback(interaction)
            return False

        if custom_id == "card_done":
            await self.custom_callback(interaction)
            return False

        if custom_id in ("card_customize_msg", "card_edit_msg"):
            modal = CustomBirthdayMessageModal(
                callback=self._on_msg_modal_submit,
                locale=self.locale,
                current_title=self.state.get("custom_message_title"),
                current_content=self.state.get("custom_message_content"),
            )
            await interaction.response.send_modal(modal)
            return False

        if custom_id == "card_reset_msg":
            self.state["use_custom_message"] = "default"
            self.state["custom_message_title"] = None
            self.state["custom_message_content"] = None
            self._render()
            await interaction.response.edit_message(view=self)
            return False

        if custom_id in ("card_customize_img", "card_edit_img"):
            modal_title = ml("buttons.summary-card.modal-title-image", locale=self.locale) or "Custom Image"
            self._pending_file_modal = FileUploadModal(
                callback=self._on_img_modal_submit,
                locale=self.locale,
                title=modal_title,
            )
            await interaction.response.send_modal(self._pending_file_modal)
            return False

        if custom_id == "card_reset_img":
            self.state["use_custom_image"] = "default"
            self.state["custom_image"] = None
            self._render()
            await interaction.response.edit_message(view=self)
            return False

        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"BirthdaySummaryCardView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )
