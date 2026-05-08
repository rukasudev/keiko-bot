from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import discord

from app import logger
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import Style
from app.services.utils import format_values_by_style, ml


@dataclass
class HeaderLine:
    emoji: str
    label: str
    value: str


@dataclass
class SummaryCardHeader:
    title: str
    lines: List[HeaderLine]
    thumbnail_url: str


@dataclass
class CustomizableSection:
    key: str
    icon: str
    label: str
    is_custom: Callable[[Dict[str, Any]], bool]
    render_preview: Callable[["SummaryCardView", Dict[str, Any], discord.ui.Container], None]
    open_customize: Callable[[discord.Interaction, "SummaryCardView"], Awaitable[None]]
    reset: Callable[[Dict[str, Any]], None]
    customize_label: str = ""


@dataclass
class SummaryCardConfig:
    header: SummaryCardHeader
    sections: List[CustomizableSection]
    initial_state: Dict[str, Any] = field(default_factory=dict)


class SummaryCardView(discord.ui.LayoutView):
    def __init__(
        self,
        config: SummaryCardConfig,
        on_done: Callable[[discord.Interaction, Dict[str, Any]], Awaitable[None]],
        locale: str,
        on_back: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(timeout=1800)
        self.config = config
        self._on_done = on_done
        self.locale = locale
        self._on_back = on_back
        self.state: Dict[str, Any] = dict(config.initial_state)

        self._render()

    def get_response(self) -> Dict[str, Any]:
        return self.state

    def _render(self) -> None:
        for item in list(self.children):
            self.remove_item(item)

        container = discord.ui.Container(accent_colour=discord.Colour(int(Style.BACKGROUND_COLOR, 16)))

        header = self.config.header
        header_lines = [f"## {header.title}"] + [
            f"{line.emoji} **{line.label}:** {line.value}" for line in header.lines
        ]
        container.add_item(discord.ui.Section(
            *header_lines,
            accessory=discord.ui.Thumbnail(header.thumbnail_url),
        ))
        container.add_item(discord.ui.Separator())

        for index, section in enumerate(self.config.sections):
            self._render_section(container, section, index)
            container.add_item(discord.ui.Separator())

        action_buttons = []
        done_label = ml("buttons.summary-card.done", locale=self.locale)
        action_buttons.append(discord.ui.Button(label=done_label, custom_id="card_done", style=discord.ButtonStyle.green))

        cancel_label = ml("buttons.cancel.label", locale=self.locale)
        action_buttons.append(discord.ui.Button(label=cancel_label, custom_id="card_cancel", style=discord.ButtonStyle.red))

        if self._on_back:
            back_label = ml("buttons.back.label", locale=self.locale)
            action_buttons.append(discord.ui.Button(label=back_label, custom_id="card_back", style=discord.ButtonStyle.secondary))

        container.add_item(discord.ui.ActionRow(*action_buttons))
        self.add_item(container)

    def _render_section(self, container: discord.ui.Container, section: CustomizableSection, index: int) -> None:
        is_custom = section.is_custom(self.state)
        if is_custom:
            badge = ml("buttons.summary-card.badge-custom", locale=self.locale)
        else:
            badge = ml("buttons.summary-card.badge-default", locale=self.locale)

        container.add_item(discord.ui.TextDisplay(f"{section.icon} **{section.label}:** {badge}"))
        section.render_preview(self, self.state, container)

        if is_custom:
            edit_label = ml("buttons.summary-card.edit", locale=self.locale)
            reset_label = ml("buttons.summary-card.reset", locale=self.locale)
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(label=edit_label, custom_id=f"card_edit_{index}", style=discord.ButtonStyle.grey),
                discord.ui.Button(label=reset_label, custom_id=f"card_reset_{index}", style=discord.ButtonStyle.grey),
            ))
        else:
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(label=section.customize_label, custom_id=f"card_customize_{index}", style=discord.ButtonStyle.grey),
            ))

    def update_state(self, **changes) -> None:
        self.state.update(changes)
        self._render()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id", "")

        if custom_id == "card_cancel":
            await interaction.response.defer()
            await interaction.delete_original_response()
            self.stop()
            return False

        if custom_id == "card_back" and self._on_back:
            await self._on_back(interaction)
            return False

        if custom_id == "card_done":
            await self._on_done(interaction, self.state)
            return False

        for index, section in enumerate(self.config.sections):
            if custom_id in (f"card_customize_{index}", f"card_edit_{index}"):
                await section.open_customize(interaction, self)
                return False
            if custom_id == f"card_reset_{index}":
                section.reset(self.state)
                self._render()
                await interaction.response.edit_message(view=self)
                return False

        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"SummaryCardView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


def _localized(value: Any, locale: str, default: str = "") -> str:
    if isinstance(value, dict):
        return value.get(locale) or value.get("en-us") or default
    return value if value is not None else default


def prior_state_from_form(
    responses: list,
    cogs: Optional[Dict[str, Any]],
    keys: List[str],
) -> Optional[Dict[str, Any]]:
    state: Dict[str, Any] = {}
    for key in keys:
        value = next((r.get("value") for r in responses if r["key"] == key), None)
        if isinstance(value, dict):
            value = value.get("value")
        if value is None and isinstance(cogs, dict):
            cog_entry = cogs.get(key)
            value = cog_entry.get("value") if isinstance(cog_entry, dict) else cog_entry
        state[key] = value

    if not any(state.values()):
        return None

    return state


def _resolve_value(spec: Dict[str, Any], form, interaction: discord.Interaction, locale: str) -> Any:
    source = spec.get("from", "response")
    if source == "response":
        key = spec.get("key")
        value = next(
            (r.get("_raw_value", r.get("value")) for r in form.responses if r.get("key") == key),
            None,
        )
        fmt = spec.get("format")
        if fmt and value is not None:
            return format_values_by_style(value, fmt, locale)
        return value if value is not None else ""
    if source == "interaction":
        attr_path = spec.get("attr") or ""
        obj: Any = interaction
        for part in attr_path.split("."):
            if obj is None:
                return ""
            obj = getattr(obj, part, None)
        return obj if obj is not None else ""
    return ""


def _apply_template_vars(text: Optional[str], vars_dict: Dict[str, Any]) -> str:
    if not text:
        return text or ""
    for key, value in vars_dict.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _build_title_content_section(
    section_cfg: Dict[str, Any],
    form,
    interaction: discord.Interaction,
    locale: str,
    template_vars_resolver: Callable[[str], Dict[str, Any]],
) -> CustomizableSection:
    state_cfg = section_cfg.get("state", {})
    mode_state_key = state_cfg["mode"]
    title_state_key = state_cfg["title"]
    content_state_key = state_cfg["content"]

    def is_custom(state: Dict[str, Any]) -> bool:
        return state.get(mode_state_key) == "custom"

    def render_preview(view: SummaryCardView, state: Dict[str, Any], container: discord.ui.Container) -> None:
        default_cfg = section_cfg.get("default", {}) or {}
        if is_custom(state):
            title = state.get(title_state_key) or ""
            content = state.get(content_state_key) or ""
        else:
            title = _localized(default_cfg.get("title"), view.locale)
            content = _localized(default_cfg.get("content"), view.locale)

        vars_dict = template_vars_resolver(view.locale)
        title = _apply_template_vars(title, vars_dict)
        content = _apply_template_vars(content, vars_dict)

        lines = []
        if title:
            lines.append(f"> **{title}**")
        if content:
            lines.append(f"> {content}")
        if lines:
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))

    async def open_customize(modal_interaction: discord.Interaction, view: SummaryCardView) -> None:
        from app.components.modals import TitleContentModal

        async def on_submit(submit_interaction: discord.Interaction, title: str, content: str) -> None:
            view.update_state(**{
                mode_state_key: "custom",
                title_state_key: title,
                content_state_key: content,
            })
            await submit_interaction.response.edit_message(view=view)

        modal_cfg = section_cfg.get("modal", {}) or {}
        modal = TitleContentModal(
            title=_localized(modal_cfg.get("title"), view.locale),
            title_label=_localized(modal_cfg.get("title-label"), view.locale),
            content_label=_localized(modal_cfg.get("content-label"), view.locale),
            callback=on_submit,
            current_title=view.state.get(title_state_key),
            current_content=view.state.get(content_state_key),
        )
        await modal_interaction.response.send_modal(modal)

    def reset(state: Dict[str, Any]) -> None:
        state[mode_state_key] = "default"
        state[title_state_key] = None
        state[content_state_key] = None

    return CustomizableSection(
        key=section_cfg["key"],
        icon=section_cfg["icon"],
        label=_localized(section_cfg.get("label"), locale),
        is_custom=is_custom,
        render_preview=render_preview,
        open_customize=open_customize,
        reset=reset,
        customize_label=_localized(section_cfg.get("customize-label"), locale),
    )


def _build_file_upload_section(
    section_cfg: Dict[str, Any],
    form,
    interaction: discord.Interaction,
    locale: str,
    template_vars_resolver: Callable[[str], Dict[str, Any]],
) -> CustomizableSection:
    state_cfg = section_cfg.get("state", {})
    mode_state_key = state_cfg["mode"]
    url_state_key = state_cfg["url"]

    def is_custom(state: Dict[str, Any]) -> bool:
        return state.get(mode_state_key) == "custom" and bool(state.get(url_state_key))

    def render_preview(view: SummaryCardView, state: Dict[str, Any], container: discord.ui.Container) -> None:
        if is_custom(state):
            container.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem(media=state[url_state_key]))
            )

    async def open_customize(modal_interaction: discord.Interaction, view: SummaryCardView) -> None:
        from app.components.select_views import FileUploadModal

        file_modal_holder: Dict[str, Any] = {}

        async def on_submit(submit_interaction: discord.Interaction) -> None:
            url = file_modal_holder["modal"].get_response()
            if url:
                view.update_state(**{mode_state_key: "custom", url_state_key: url})
            await submit_interaction.response.edit_message(view=view)

        modal_cfg = section_cfg.get("modal", {}) or {}
        file_modal_holder["modal"] = FileUploadModal(
            callback=on_submit,
            locale=view.locale,
            title=_localized(modal_cfg.get("title"), view.locale),
        )
        await modal_interaction.response.send_modal(file_modal_holder["modal"])

    def reset(state: Dict[str, Any]) -> None:
        state[mode_state_key] = "default"
        state[url_state_key] = None

    return CustomizableSection(
        key=section_cfg["key"],
        icon=section_cfg["icon"],
        label=_localized(section_cfg.get("label"), locale),
        is_custom=is_custom,
        render_preview=render_preview,
        open_customize=open_customize,
        reset=reset,
        customize_label=_localized(section_cfg.get("customize-label"), locale),
    )


SECTION_TYPES: Dict[str, Callable[..., CustomizableSection]] = {
    "title-content": _build_title_content_section,
    "file-upload": _build_file_upload_section,
}


def _collect_state_keys(sections_cfg: List[Dict[str, Any]]) -> List[str]:
    keys: List[str] = []
    for section in sections_cfg:
        for value in section.get("state", {}).values():
            if value and value not in keys:
                keys.append(value)
    return keys


def _apply_mode_fallback(initial_state: Dict[str, Any], sections_cfg: List[Dict[str, Any]]) -> None:
    for section in sections_cfg:
        state_cfg = section.get("state", {})
        mode_key = state_cfg.get("mode")
        if not mode_key:
            continue
        if initial_state.get(mode_key) in ("default", "custom"):
            continue
        payload_keys = [v for k, v in state_cfg.items() if k != "mode" and v]
        has_payload = any(initial_state.get(p) for p in payload_keys)
        initial_state[mode_key] = "custom" if has_payload else "default"


def _build_header(
    header_cfg: Dict[str, Any],
    form,
    interaction: discord.Interaction,
    locale: str,
) -> SummaryCardHeader:
    title_emoji = header_cfg.get("title-emoji", "")
    title_text = _localized(header_cfg.get("title"), locale)
    title = f"{title_emoji} {title_text}".strip()

    thumbnail_name = header_cfg.get("thumbnail", "")
    thumbnail_url = getattr(KeikoIcons, thumbnail_name, "") if thumbnail_name else ""

    lines = []
    for line_cfg in header_cfg.get("lines", []):
        spec = {
            "from": line_cfg.get("value-from", "response"),
            "key": line_cfg.get("value-key"),
            "attr": line_cfg.get("value-attr"),
            "format": line_cfg.get("value-format"),
        }
        lines.append(HeaderLine(
            emoji=line_cfg.get("emoji", ""),
            label=_localized(line_cfg.get("label"), locale),
            value=str(_resolve_value(spec, form, interaction, locale)),
        ))

    return SummaryCardHeader(title=title, lines=lines, thumbnail_url=thumbnail_url)


def build_summary_card_from_step(step: Dict[str, Any], form, interaction: discord.Interaction) -> SummaryCardView:
    locale = form.locale
    sections_cfg = step.get("sections", []) or []

    header = _build_header(step.get("header", {}), form, interaction, locale)

    template_vars_specs = step.get("template-vars", {}) or {}

    def resolve_template_vars(current_locale: str) -> Dict[str, Any]:
        return {
            key: _resolve_value(spec, form, interaction, current_locale)
            for key, spec in template_vars_specs.items()
        }

    state_keys = _collect_state_keys(sections_cfg)
    initial_state = prior_state_from_form(form.responses, form.cogs, state_keys) or {}
    for key in state_keys:
        initial_state.setdefault(key, None)
    _apply_mode_fallback(initial_state, sections_cfg)

    sections: List[CustomizableSection] = []
    for section_cfg in sections_cfg:
        handler = SECTION_TYPES.get(section_cfg.get("type"))
        if not handler:
            logger.warn(
                f"Unknown summary card section type: {section_cfg.get('type')}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue
        sections.append(handler(section_cfg, form, interaction, locale, resolve_template_vars))

    config = SummaryCardConfig(header=header, sections=sections, initial_state=initial_state)

    async def on_done(done_interaction: discord.Interaction, _state: Dict[str, Any]) -> None:
        await form._callback(done_interaction)

    return SummaryCardView(
        config=config,
        on_done=on_done,
        locale=locale,
        on_back=form._go_back if form.state.can_go_back else None,
    )
