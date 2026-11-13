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

from app.components.embed import (
    card_footer,
    card_frame,
    card_header,
    card_row,
    with_footer,
)
from app.constants import DiscordLimits, Style
from app.constants import ViewConstants as view_constants
from app.core import members
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
    SectionView,
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
            label=spec.label or None,
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

    async def callback(self, interaction: discord.Interaction) -> None:
        """Keep the picked members at hand for their pictures, then report them."""
        for member in self.values:
            members.remember(member)
        await super().callback(interaction)


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
            min_values=1 if component.required else 0,
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
        discord.ui.ActionRow(*items[index : index + DiscordLimits.BUTTONS_PER_ROW])
        for index in range(0, len(items), DiscordLimits.BUTTONS_PER_ROW)
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
        _card_section(container, section, ids, dispatcher)
        container.add_item(discord.ui.Separator())


def _card_section(
    container: discord.ui.Container[Any],
    section: SectionView,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    texts = (section.heading, section.description, *section.preview)
    shown = "\n".join(line for line in texts if line)
    container.add_item(discord.ui.TextDisplay(shown))

    if section.media and section.preview:
        _picture(container, section.media)
    for control in section.controls:
        if isinstance(control, Choice):
            _choice(container, control, ids, dispatcher)
            continue
        container.add_item(discord.ui.ActionRow(_select_item(dispatcher, ids, control)))
    if section.media and not section.preview:
        _picture(container, section.media)
    if section.buttons:
        buttons = [_button(dispatcher, ids, spec) for spec in section.buttons]
        container.add_item(discord.ui.ActionRow(*buttons))


def _picture(container: discord.ui.Container[Any], media: str) -> None:
    container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media=media)))


def _panel(
    container: discord.ui.Container[Any], panel: Panel, ids: Ids, dispatcher: Dispatcher
) -> None:
    card_header(container, panel.title, panel.intro, panel.thumbnail)

    for group in panel.groups:
        if group.parts:
            _panel_parts(container, panel, group, ids, dispatcher)
        elif group.actions:
            if group.lines:
                body = "\n".join(group.lines)
                card_row(container, body, separated=bool(group.heading))
        elif group.lines:
            accessory = None
            if group.key:
                spec = Button(panel.edit_label, f"edit:{group.key}", "secondary", "✏️")
                accessory = _button(dispatcher, ids, spec)
            card_row(container, "\n".join(group.lines), accessory)
        if group.media:
            container.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem(media=group.media))
            )
        if group.actions:
            row = [_button(dispatcher, ids, spec) for spec in group.actions]
            container.add_item(discord.ui.ActionRow(*row))
        _toggles(container, group, ids, dispatcher)

    if panel.info:
        heading = f"### {panel.info_title}\n" if panel.info_title else ""
        action = panel.info_action
        accessory = _button(dispatcher, ids, action) if action is not None else None
        card_row(container, f"{heading}{panel.info}", accessory)
    container.add_item(discord.ui.Separator())


def _toggles(
    container: discord.ui.Container[Any],
    group: PanelGroup,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    """One button per choice, lit when it is on and asking for the other state."""
    items = [
        _button(
            dispatcher,
            ids,
            Button(
                option.label,
                f"{'turn_off' if option.selected else 'turn_on'}:"
                f"{group.choice_target}={option.value}",
                "success" if option.selected else "secondary",
            ),
        )
        for option in group.choices
    ]
    row = view_constants.CHOICE_BUTTONS_PER_ROW
    for index in range(0, len(items), row):
        container.add_item(discord.ui.ActionRow(*items[index : index + row]))


def _panel_parts(
    container: discord.ui.Container[Any],
    panel: Panel,
    group: PanelGroup,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    if group.lines:
        card_row(container, "\n".join(group.lines))
    for index, part in enumerate(group.parts):
        spec = Button(
            part.label or panel.edit_label,
            part.button,
            "secondary",
            part.emoji,
            part.disabled,
            part.description,
        )
        card_row(
            container,
            "\n".join(part.lines),
            _button(dispatcher, ids, spec),
            separated=index == 0 and not group.lines,
        )
        if part.body:
            container.add_item(discord.ui.TextDisplay("\n".join(part.body)))
        if part.actions:
            row = [_button(dispatcher, ids, action) for action in part.actions]
            container.add_item(discord.ui.ActionRow(*row))


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
        elif isinstance(component, Choice):
            _choice(container, component, ids, dispatcher)


def _choice(
    container: discord.ui.Container[Any],
    choice: Choice,
    ids: Ids,
    dispatcher: Dispatcher,
) -> None:
    action = f"set:{choice.slot}=" if choice.slot else "pick:"
    if choice.button_label:
        for option in choice.options:
            style = "primary" if option.selected else option.style
            spec = Button(choice.button_label, f"{action}{option.value}", style)
            body = f"**{option.label}**\n{option.description}"
            card_row(container, body, _button(dispatcher, ids, spec))
        return
    buttons = [
        _button(
            dispatcher,
            ids,
            Button(option.label, f"{action}{option.value}", option.style),
        )
        for option in choice.options
    ]
    row = view_constants.CHOICE_BUTTONS_PER_ROW
    for index in range(0, len(buttons), row):
        container.add_item(discord.ui.ActionRow(*buttons[index : index + row]))


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
    container = card_frame(accent)
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
        card_footer(container, screen.layout_footer)
    view.add_item(container)
    action_buttons = [
        _button(dispatcher, ids, spec) for spec in cancel_last(screen.buttons)
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


def walk(view: Any) -> list[Any]:
    """Every item of `view`, depth first, a section's accessory after the section."""
    found: list[Any] = []

    def visit(node: Any) -> None:
        for item in getattr(node, "children", None) or []:
            found.append(item)
            visit(item)
            accessory = getattr(item, "accessory", None)

            if accessory is not None:
                found.append(accessory)

    if view is not None:
        visit(view)
    return found


def disable(view: Any) -> tuple[Any, ...]:
    """Disable every control of `view` that is enabled, and return those controls."""
    kinds = (
        discord.ui.Button,
        discord.ui.Select,
        discord.ui.ChannelSelect,
        discord.ui.RoleSelect,
        discord.ui.UserSelect,
        discord.ui.MentionableSelect,
    )
    controls = tuple(
        item for item in walk(view) if isinstance(item, kinds) and not item.disabled
    )
    for control in controls:
        control.disabled = True
    return controls


def enable(controls: Sequence[Any]) -> None:
    """Enable the controls `disable` returned."""
    for control in controls:
        control.disabled = False


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
