"""From a `Screen` to discord.py objects: an embed and a View, a LayoutView, a Modal.

Every component carries a session-coded custom_id and reports its click to
one dispatcher; the views hold no state. Cancel renders last by construction,
and a Components V2 tree never carries an embed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from io import BytesIO
from typing import Any, cast

import discord

from app.components.embed import with_footer
from app.constants import DiscordLimits, Style
from app.constants import ViewConstants as view_constants
from app.settings.discord import layout
from app.settings.discord.interactions import encode
from app.settings.form.components import (
    Button,
    Card,
    Choice,
    FileInput,
    Gallery,
    OptionSelect,
    Panel,
    PanelGroup,
    Picker,
    Screen,
    TextInputs,
    cancel_last,
)

Dispatch = Callable[[discord.Interaction, str, Any], Awaitable[None]]

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}
BUTTONS_PER_ROW = 5


class Dispatcher:
    """Where every rendered component sends its interaction."""

    def __init__(self, handle: Dispatch) -> None:
        self.handle = handle


class ActionButton(discord.ui.Button[Any]):
    """A button that reports its action and nothing else."""

    def __init__(
        self, dispatcher: Dispatcher, custom_id: str, spec: Button, **kwargs: Any
    ):
        super().__init__(
            label=spec.label,
            style=BUTTON_STYLES[spec.style],
            emoji=spec.emoji,
            disabled=spec.disabled,
            custom_id=custom_id,
            **kwargs,
        )
        self.dispatcher = dispatcher
        self.desc = spec.description
        self.step_key = (
            spec.action.split(":", 1)[1] if spec.action.startswith("edit:") else None
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Report the click."""
        await self.dispatcher.handle(interaction, self.custom_id or "", None)


class ValuesSelect:
    """What every select of ours shares: reporting its values on change."""

    dispatcher: Dispatcher

    async def callback(self, interaction: discord.Interaction) -> None:
        """Report the chosen values."""
        select = cast(Any, self)
        chosen = [str(getattr(value, "id", value)) for value in select.values]
        await self.dispatcher.handle(interaction, select.custom_id or "", chosen)


class ChannelPicker(ValuesSelect, discord.ui.ChannelSelect[Any]):
    """A native channel select."""

    def __init__(
        self, dispatcher: Dispatcher, custom_id: str, spec: Picker, **kwargs: Any
    ):
        super().__init__(
            custom_id=custom_id,
            placeholder=spec.placeholder,
            channel_types=[discord.ChannelType.text],
            default_values=[
                discord.SelectDefaultValue(
                    id=int(value), type=discord.SelectDefaultValueType.channel
                )
                for value in spec.selected
                if value.isdigit()
            ],
            **kwargs,
        )
        self.dispatcher = dispatcher


class RolePicker(ValuesSelect, discord.ui.RoleSelect[Any]):
    """A native role select."""

    def __init__(
        self, dispatcher: Dispatcher, custom_id: str, spec: Picker, **kwargs: Any
    ):
        super().__init__(
            custom_id=custom_id,
            placeholder=spec.placeholder,
            default_values=[
                discord.SelectDefaultValue(
                    id=int(value), type=discord.SelectDefaultValueType.role
                )
                for value in spec.selected
                if value.isdigit()
            ],
            **kwargs,
        )
        self.dispatcher = dispatcher


class UserPicker(ValuesSelect, discord.ui.UserSelect[Any]):
    """A native member select."""

    def __init__(
        self, dispatcher: Dispatcher, custom_id: str, spec: Picker, **kwargs: Any
    ):
        super().__init__(
            custom_id=custom_id,
            placeholder=spec.placeholder,
            default_values=[
                discord.SelectDefaultValue(
                    id=int(value), type=discord.SelectDefaultValueType.user
                )
                for value in spec.selected
                if value.isdigit()
            ],
            **kwargs,
        )
        self.dispatcher = dispatcher


class OptionsPicker(ValuesSelect, discord.ui.Select[Any]):
    """A select over declared options."""

    def __init__(
        self, dispatcher: Dispatcher, custom_id: str, spec: OptionSelect, **kwargs: Any
    ):
        super().__init__(
            custom_id=custom_id,
            placeholder=_fit(spec.placeholder),
            min_values=spec.min_values,
            max_values=spec.max_values,
            options=[
                discord.SelectOption(
                    label=_fit(option.label) or "-",
                    value=option.value,
                    description=_fit(option.description) or None,
                    default=option.selected,
                )
                for option in spec.options
            ],
            **kwargs,
        )
        self.dispatcher = dispatcher


PICKERS = {"channel": ChannelPicker, "role": RolePicker, "user": UserPicker}


def _fit(text: str | None) -> str | None:
    if text in (None, ""):
        return None
    assert text is not None
    if len(text) <= DiscordLimits.SELECT_OPTION_TEXT:
        return text
    return f"{text[: DiscordLimits.SELECT_OPTION_TEXT - 1]}…"


def _select_item(
    dispatcher: Dispatcher, ids: Ids, component: Any
) -> discord.ui.Item[Any]:
    slot = component.slot or component.step_key
    custom_id = ids(component.action, slot)

    if isinstance(component, Picker):
        picker_type = PICKERS[component.kind]
        return picker_type(
            dispatcher,
            custom_id,
            component,
            min_values=1 if component.required or not component.unique else 0,
            max_values=1 if component.unique else 25,
        )
    assert isinstance(component, OptionSelect)
    return OptionsPicker(dispatcher, custom_id, component)


ANSWERING = ("confirm", "done")


class Ids:
    """Custom ids of one screen: the session, its revision and the step on show."""

    def __init__(self, session_id: str, revision: int, cursor: str | None = None):
        self.session_id = session_id
        self.revision = revision
        self.cursor = cursor

    def __call__(self, action: str, arg: str | None = None) -> str:
        """The custom id of `action` on this screen; an answer names its step."""
        if arg is None and action in ANSWERING:
            arg = self.cursor
        return encode(self.session_id, self.revision, action, arg)


def _button(
    dispatcher: Dispatcher, ids: Ids, spec: Button, **kwargs: Any
) -> ActionButton:
    action, _, arg = spec.action.partition(":")
    return ActionButton(dispatcher, ids(action, arg or None), spec, **kwargs)


def embed_of(screen: Screen) -> discord.Embed:
    """The embed of an embed-flavoured screen."""
    embed = discord.Embed(
        color=int(screen.color or Style.BACKGROUND_COLOR, base=16),
        title=screen.title or None,
        description=screen.description or None,
    )
    for field in screen.fields:
        embed.add_field(name=field.name, value=field.value, inline=False)
    if screen.thumbnail:
        embed.set_thumbnail(url=screen.thumbnail)
    if screen.image:
        embed.set_image(url=screen.image)
    with_footer(embed, screen.footer)
    return embed


def view_of(screen: Screen, ids: Ids, dispatcher: Dispatcher) -> discord.ui.View:
    """The classic view of an embed-flavoured screen: components, then buttons."""
    view = discord.ui.View(timeout=view_constants.LONG_TIMEOUT_SECONDS)
    for component in screen.components:
        if isinstance(component, Choice):
            for option in component.options:
                spec = Button(
                    option.label,
                    f"pick:{option.value}",
                    "primary" if option.selected else option.style,
                )
                view.add_item(_button(dispatcher, ids, spec))
        elif isinstance(component, (Picker, OptionSelect)):
            view.add_item(_select_item(dispatcher, ids, component))
    for spec in cancel_last(screen.buttons):
        view.add_item(_button(dispatcher, ids, spec))
    return view


def _action_rows(
    items: Sequence[discord.ui.Item[Any]],
) -> list[discord.ui.ActionRow[Any]]:
    return [
        discord.ui.ActionRow(*items[index : index + BUTTONS_PER_ROW])
        for index in range(0, len(items), BUTTONS_PER_ROW)
    ]


def _gallery(
    container: discord.ui.Container[Any],
    gallery: Gallery,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    container.add_item(discord.ui.TextDisplay(gallery.header))
    container.add_item(discord.ui.Separator())

    for index, design in enumerate(gallery.designs):
        container.add_item(
            discord.ui.TextDisplay(f"**{design.label}**\n{design.description}")
        )
        if design.preview_url:
            container.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(media=design.preview_url)
                )
            )
        button = _button(
            dispatcher,
            ids,
            Button(gallery.select_label, f"design:{design.key}", "primary"),
        )
        container.add_item(discord.ui.ActionRow(button))

        if index < len(gallery.designs) - 1:
            container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(gallery.footer))
    container.add_item(discord.ui.Separator())


def _card(
    container: discord.ui.Container[Any], card: Card, ids: Ids, dispatcher: Dispatcher
) -> None:
    header = card.header
    lines = [f"## {header.title}"]

    if header.description:
        lines.append(header.description)
    lines += list(header.lines)
    if header.thumbnail:
        container.add_item(
            discord.ui.Section(
                *lines[:3], accessory=discord.ui.Thumbnail(header.thumbnail)
            )
        )
        for line in lines[3:]:
            container.add_item(discord.ui.TextDisplay(line))
    else:
        for line in lines:
            container.add_item(discord.ui.TextDisplay(line))
    container.add_item(discord.ui.Separator())
    for section in card.sections:
        container.add_item(discord.ui.TextDisplay(section.heading))
        if section.preview:
            container.add_item(discord.ui.TextDisplay("\n".join(section.preview)))
        if section.media:
            container.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem(media=section.media))
            )
        buttons = [_button(dispatcher, ids, spec) for spec in section.buttons]
        container.add_item(discord.ui.ActionRow(*buttons))
        container.add_item(discord.ui.Separator())


def _panel(
    container: discord.ui.Container[Any], panel: Panel, ids: Ids, dispatcher: Dispatcher
) -> None:
    layout.header(container, panel.title, panel.intro, panel.thumbnail)

    for group in panel.groups:
        if group.parts:
            _panel_parts(container, panel, group, ids, dispatcher)
        elif group.actions:
            container.add_item(discord.ui.TextDisplay("\n".join(group.lines)))
        else:
            accessory = None
            if group.key:
                spec = Button(panel.edit_label, f"edit:{group.key}", "secondary", "✏️")
                accessory = _button(dispatcher, ids, spec)
            layout.row(container, "\n".join(group.lines), accessory)
        if group.actions:
            row = [_button(dispatcher, ids, spec) for spec in group.actions]
            container.add_item(discord.ui.ActionRow(*row))
        _toggles(container, group, ids, dispatcher)

    if panel.info:
        heading = f"### {panel.info_title}\n" if panel.info_title else ""
        layout.row(container, f"{heading}{panel.info}")
    container.add_item(discord.ui.Separator())


def _toggles(
    container: discord.ui.Container[Any],
    group: PanelGroup,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    """One button per choice, lit when it is on, four to a row."""
    items = [
        _button(
            dispatcher,
            ids,
            Button(
                option.label,
                f"toggle:{group.choice_target}={option.value}",
                "success" if option.selected else "secondary",
            ),
        )
        for option in group.choices
    ]
    for index in range(0, len(items), 4):
        container.add_item(discord.ui.ActionRow(*items[index : index + 4]))


def _panel_parts(
    container: discord.ui.Container[Any],
    panel: Panel,
    group: PanelGroup,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    if group.lines:
        layout.row(container, "\n".join(group.lines))
    for index, part in enumerate(group.parts):
        spec = Button(
            part.label or panel.edit_label, part.button, "secondary", part.emoji
        )
        layout.row(
            container,
            "\n".join(part.lines),
            _button(dispatcher, ids, spec),
            separated=index == 0 and not group.lines,
        )


def _picker_screen(
    container: discord.ui.Container[Any],
    screen: Screen,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    container.add_item(discord.ui.TextDisplay(f"## {screen.title}"))
    if screen.description:
        container.add_item(discord.ui.TextDisplay(screen.description))
    for component in screen.components:
        if isinstance(component, (Picker, OptionSelect)):
            container.add_item(
                discord.ui.ActionRow(_select_item(dispatcher, ids, component))
            )
    option_buttons = [
        spec for spec in screen.buttons if spec.action.startswith("pick:")
    ]
    for index in range(0, len(option_buttons), 4):
        row = [
            _button(dispatcher, ids, spec) for spec in option_buttons[index : index + 4]
        ]
        container.add_item(discord.ui.ActionRow(*row))


def layout_of(
    screen: Screen, ids: Ids, dispatcher: Dispatcher
) -> discord.ui.LayoutView:
    """The Components V2 view of a layout-flavoured screen."""
    view = discord.ui.LayoutView(timeout=view_constants.LONG_TIMEOUT_SECONDS)
    accent = (
        discord.Colour.blurple()
        if any(isinstance(component, Gallery) for component in screen.components)
        else None
    )
    container = layout.container(accent)
    is_picker = not screen.components or not isinstance(
        screen.components[0], (Card, Panel, Gallery)
    )

    for component in screen.components:
        if isinstance(component, Card):
            _card(container, component, ids, dispatcher)
        elif isinstance(component, Panel):
            _panel(container, component, ids, dispatcher)
        elif isinstance(component, Gallery):
            _gallery(container, component, ids, dispatcher)
    if is_picker:
        _picker_screen(container, screen, ids, dispatcher)
    if screen.layout_footer:
        layout.footer(container, screen.layout_footer)
    view.add_item(container)
    action_buttons = [
        _button(dispatcher, ids, spec)
        for spec in cancel_last(screen.buttons)
        if not spec.action.startswith("pick:")
    ]

    for row in _action_rows(action_buttons):
        view.add_item(row)
    return view


class FormModal(discord.ui.Modal):
    """A modal that reports its inputs, or the url of its uploaded file."""

    def __init__(
        self,
        screen: Screen,
        ids: Ids,
        dispatcher: Dispatcher,
        action: str,
        arg: str | None,
    ) -> None:
        super().__init__(
            title=screen.modal_title[: DiscordLimits.MODAL_TITLE],
            timeout=view_constants.SHORT_TIMEOUT_SECONDS,
        )
        self.dispatcher = dispatcher
        self.custom_id = ids(action, arg)
        self.inputs: list[discord.ui.TextInput[Any]] = []
        self.file_upload: discord.ui.FileUpload[Any] | None = None

        for component in screen.components:
            if isinstance(component, TextInputs):
                for spec in component.inputs:
                    item: discord.ui.TextInput[Any] = discord.ui.TextInput(
                        label=spec.label[: DiscordLimits.MODAL_INPUT_LABEL],
                        style=discord.TextStyle.long
                        if spec.multiline
                        else discord.TextStyle.short,
                        placeholder=spec.placeholder,
                        default=spec.default,
                        required=spec.required,
                        max_length=spec.max_length,
                    )
                    self.inputs.append(item)
                    self.add_item(item)
            elif isinstance(component, FileInput):
                self.file_upload = discord.ui.FileUpload(
                    custom_id="custom_image_upload"
                )
                self.add_item(
                    discord.ui.Label(text=component.label, component=self.file_upload)
                )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Report the typed inputs, or the uploaded file's permanent url."""
        if self.file_upload is not None:
            url = (
                await upload_attachment(self.file_upload.values[0])
                if self.file_upload.values
                else None
            )
            await self.dispatcher.handle(interaction, self.custom_id, {"url": url})
            return
        await self.dispatcher.handle(
            interaction,
            self.custom_id,
            {"inputs": [item.value for item in self.inputs]},
        )


async def upload_attachment(attachment: Any) -> str:
    """Re-upload an attachment to the dump channel and return its permanent url."""
    import app as app_module

    bot = cast(Any, app_module).bot
    file_bytes = await attachment.read()
    dump_channel = bot.get_channel(bot.config.ADMIN_DUMP_CHANNEL_ID)
    message = await dump_channel.send(
        file=discord.File(fp=BytesIO(file_bytes), filename=attachment.filename)
    )
    return str(message.attachments[0].url)


def modal_of(
    screen: Screen, ids: Ids, dispatcher: Dispatcher, action: str, arg: str | None
) -> FormModal:
    """The modal of a modal-flavoured screen."""
    return FormModal(screen, ids, dispatcher, action, arg)


def finalized_layout(view: discord.ui.LayoutView) -> discord.ui.LayoutView:
    """The same container without its action rows: the content stays, the buttons go."""

    def strip(parent: Any) -> None:
        for child in list(getattr(parent, "children", [])):
            if isinstance(child, discord.ui.ActionRow):
                parent.remove_item(child)
            elif getattr(child, "children", None) is not None:
                strip(child)

    strip(view)
    return view
