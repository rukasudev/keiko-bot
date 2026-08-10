"""The manager panel: a command's saved configuration, as a Components V2
container.

Same shape as the configuration card (`app/views/summary_card.py`): a container
with an accent bar, a header with the thumbnail, real separators between
sections, and each group of settings sitting next to the button that edits it.
An embed can only stack text; a container can put the action where the thing it
acts on is, which is what makes the panel read as an interface instead of a
printout.

Sections come from the structure the YAML already declares — a card owns its
fields, a multi-select owns its selects, a composition owns itself
(`resolve_form_settings_groups` in `app/services/utils.py`) — so a command
changes its panel by editing configuration, never Python. Settings that no step
owns (a command with a custom `settings_provider`, like birthday) render as one
unlabelled block and the panel keeps the global Edit button, which is then the
only control that can reach them.

A Components V2 message can never be edited back into an embed message, so
every screen opened from here replaces the panel instead of editing it — see
`app/views/panel_transitions.py`.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import discord

from app.constants import Commands as commandconstants
from app.constants import Style
from app.services.utils import format_values_by_style, ml
from app.views.panel_transitions import transition_to_embed

BUTTONS_PER_ROW = 5


# ===========================================================================
# Writing a saved setting as a line of text
# ===========================================================================

EMOJI_PREFIX = re.compile(
    r"^(?:[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍])+\s*"
)


def without_leading_emoji(text: str) -> str:
    """An option's label carries its emoji so the button and the line that
    explains it match. Read back as a saved value it is decoration: the panel
    already says what the setting is, and a second emoji on the value only
    competes with the section it lives in."""
    return EMOJI_PREFIX.sub("", text, count=1) or text


def labelled(title: str, value: str, separator: str = " ") -> str:
    """`Title: value`, without doubling punctuation when the title is already
    a question ("Qual o Alcance da Regra?:" reads like a typo)."""
    punctuation = "" if title.rstrip().endswith(("?", ":")) else ":"
    return f"**{title}{punctuation}**{separator}{value}"


def _more_label(count: int, locale: str) -> str:
    return ml("commands.resume.more", locale=locale).replace("$count", str(count))


def _empty_label(locale: str) -> str:
    return ml("commands.resume.empty", locale=locale) or "-"


def _value_and_style(row: Dict[str, Any]):
    values = row.get("value", "-")
    style = row.get("style")
    if isinstance(values, dict):
        style = values.get("style")
        values = values.get("values", "-")
    return values, style


def _list_items(values: Any, style: Optional[str]) -> Optional[List[Any]]:
    """A list the user enumerated, as opposed to a scalar Discord will render
    as a mention or a single word."""
    if isinstance(values, (list, tuple)) and style in ("bullet", "numbered", "code"):
        return list(values)
    return None


def _composition_lines(values: List[Dict[str, Any]], locale: str) -> List[str]:
    """One entry per block: a numbered header and one line per field, so an
    entry with several fields stays readable instead of collapsing into a
    single run-on line."""
    limit = commandconstants.SETTINGS_COMPOSITION_PREVIEW_LIMIT
    lines = []
    for index, composition in enumerate(values[:limit], start=1):
        lines.append(f"**#{index}**")
        for item in composition.values():
            if not isinstance(item, dict) or item.get("hidden"):
                continue
            value = format_values_by_style(
                item.get("value"), item.get("style"), locale
            ) or "-"
            lines.append(labelled(item.get("title") or "", str(value).strip()))

    if len(values) > limit:
        lines.append(_more_label(len(values) - limit, locale))
    return lines


def row_value(row: Dict[str, Any], locale: str) -> Tuple[str, bool]:
    """(text, is_block). A value is a block only when it is a list of items —
    never because its formatting happened to contain a line break."""
    values, style = _value_and_style(row)

    if style == "composition":
        lines = _composition_lines(values or [], locale)
        return "\n".join(lines) or _empty_label(locale), True

    items = _list_items(values, style)
    if items:
        return format_values_by_style(items, "bullet", locale).lstrip("\n"), True

    formatted = format_values_by_style(values, style, locale)
    return without_leading_emoji(str(formatted or _empty_label(locale))), False


# ===========================================================================
# Grouping the saved settings into the panel's sections
# ===========================================================================


@dataclass
class SettingsGroup:
    key: Optional[str]
    title: Optional[str]
    icon: str
    rows: List[Dict[str, Any]]


def group_rows(rows: List[Dict[str, Any]]) -> List[SettingsGroup]:
    """Rows in the order they were saved, bucketed by the YAML step that owns
    them — which is the section the panel draws and the step the edit flow
    jumps to. A row no step owns falls into the group with no key, which the
    panel draws without a heading and without its own edit button."""
    buckets: List[SettingsGroup] = []
    index: Dict[Optional[str], int] = {}

    for row in rows:
        if row.get("hidden"):
            continue
        key = row.get("group")
        if key not in index:
            index[key] = len(buckets)
            buckets.append(SettingsGroup(
                key, row.get("group_title"), row.get("group_icon") or "", []
            ))
        buckets[index[key]].rows.append(row)

    return buckets


def row_is_the_whole_group(row: Dict[str, Any], group: SettingsGroup) -> bool:
    """A composition is its own section, so the section heading already says
    its name and the row must not repeat it."""
    return len(group.rows) == 1 and row.get("title") == group.title


def _same_text(first: str, second: str) -> bool:
    def normalize(text: str) -> str:
        return without_leading_emoji(text or "").strip().casefold()

    return bool(normalize(first)) and normalize(first) == normalize(second)


# ===========================================================================
# The panel
# ===========================================================================


class SectionEditButton(discord.ui.Button):
    """The ✏️ next to a group. It opens the edit flow already pointed at that
    group, so the user skips the "which setting?" dropdown entirely."""

    def __init__(self, panel: "ManagerPanelView", step_key: str, locale: str):
        self.panel = panel
        self.step_key = step_key
        self.desc = ml("buttons.edit.desc", locale=locale)
        super().__init__(
            emoji="✏️",
            label=ml("buttons.edit.label", locale=locale),
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from app.components.buttons import panel_screen_embed
        from app.views.edit import EditCommand

        manager = self.panel.manager
        view = EditCommand(
            manager.command_key, manager.cogs, manager.locale, manager.update_command
        )
        manager.edited_form_view = view.form_view
        manager._original_embed = panel_screen_embed(
            interaction, manager.command_key, manager.locale
        )

        if self.step_key in view.get_command_options():
            # The form engine leaves a Components V2 message on its own
            # (`_transition_from_layout_view`), so jumping straight into the
            # step needs nothing else from here.
            view.selected_options = [self.step_key]
            return await view.callback(interaction)

        await transition_to_embed(interaction, manager._original_embed, view)


class ManagerPanelView(discord.ui.LayoutView):
    def __init__(self, *, manager, title: str, intro: str, rows: List[Dict[str, Any]],
                 locale: str, thumbnail: str = "", footer: str = "",
                 info: str = "", info_title: str = "",
                 extra_buttons: Optional[List[discord.ui.Button]] = None):
        super().__init__(timeout=1800)
        self.manager = manager
        self.locale = locale
        self._render(title, intro, rows, thumbnail, footer, info, info_title,
                     extra_buttons or [])

    # -- the panel is what a button sees as `self.view`, the Manager is what
    # -- every manager behavior lives on: forward between the two.

    def __getattr__(self, name: str) -> Any:
        if name == "manager":
            raise AttributeError(name)
        return getattr(self.manager, name)

    @property
    def edited_form_view(self):
        return self.manager.edited_form_view

    @edited_form_view.setter
    def edited_form_view(self, value) -> None:
        self.manager.edited_form_view = value

    @property
    def form_view(self):
        return self.manager.form_view

    @form_view.setter
    def form_view(self, value) -> None:
        self.manager.form_view = value

    @property
    def _original_embed(self):
        return self.manager._original_embed

    @_original_embed.setter
    def _original_embed(self, value) -> None:
        self.manager._original_embed = value

    # ------------------------------------------------------------------ render

    def _render(self, title, intro, rows, thumbnail, footer, info, info_title,
                extra_buttons) -> None:
        container = discord.ui.Container(
            accent_colour=discord.Colour(int(Style.BACKGROUND_COLOR, 16))
        )

        header = [f"## {title}"]
        if intro:
            header.append(intro)
        if thumbnail:
            container.add_item(discord.ui.Section(
                *header, accessory=discord.ui.Thumbnail(thumbnail)
            ))
        else:
            for line in header:
                container.add_item(discord.ui.TextDisplay(line))

        groups = group_rows(rows)
        for group in groups:
            container.add_item(discord.ui.Separator())
            body = "\n".join(self._section_lines(group, title))

            if group.key:
                container.add_item(discord.ui.Section(
                    body, accessory=SectionEditButton(self, group.key, self.locale)
                ))
            else:
                container.add_item(discord.ui.TextDisplay(body))

        if info:
            container.add_item(discord.ui.Separator())
            heading = f"### {info_title}\n" if info_title else ""
            container.add_item(discord.ui.TextDisplay(f"{heading}{info}"))

        container.add_item(discord.ui.Separator())
        for row in self._action_rows(extra_buttons, groups):
            container.add_item(row)

        if footer:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"-# {footer}"))

        self.add_item(container)

    def _heading(self, group: SettingsGroup, panel_title: str) -> str:
        """The section's own line, when it has something to say.

        Two cases have nothing: a group no YAML step owns has no name, and a
        step whose title repeats the command's would draw the same words twice,
        one size apart — which is the noise that made the panel look flat in
        the first place.
        """
        if not group.title or _same_text(group.title, panel_title):
            return ""
        return f"### {group.icon} {group.title}".replace("###  ", "### ")

    def _section_lines(self, group: SettingsGroup, panel_title: str) -> List[str]:
        """One section: a heading and its settings, on two visibly different
        levels.

        The emoji belongs to the heading alone. When every line carries one,
        the emoji stops marking anything — which is exactly how a section title
        ended up looking like the setting right below it. So the heading gets
        the emoji and the size, and the settings under it are plain text.
        """
        heading = self._heading(group, panel_title)
        lines = [heading] if heading else []

        for row in group.rows:
            value, is_block = row_value(row, self.locale)
            if heading and row_is_the_whole_group(row, group):
                lines.append(value)      # the heading already named it
                continue
            # Bold on the label, not the value: the labels are the column the
            # eye runs down, and values already carry their own formatting
            # (code chips, mentions).
            separator = "\n" if is_block else " "
            lines.append(labelled(row["title"], value, separator))

        return lines

    def _action_rows(self, extra_buttons,
                     groups: List[SettingsGroup]) -> List[discord.ui.ActionRow]:
        """Every manager button. Edit is dropped only when every section
        carries its own ✏️: a command whose settings come from a
        `settings_provider` has no section to click, and the global button is
        then the only way into the edit flow."""
        from app.components.buttons import EditButton

        sections_cover_everything = bool(groups) and all(
            group.key for group in groups
        )
        buttons = [
            button for button in self.manager.children
            if not (sections_cover_everything and isinstance(button, EditButton))
        ]
        buttons += extra_buttons

        return [
            discord.ui.ActionRow(*buttons[index:index + BUTTONS_PER_ROW])
            for index in range(0, len(buttons), BUTTONS_PER_ROW)
        ]
