"""The schema of every copy file: a command's `copy.yml`, composed of the blocks the
primitives that draw it own, and each shared file of app/languages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, RootModel

from app.core.commands import Command, Open, commands
from app.core.feature_health import Stats
from app.core.i18n import CopyBlock, CopyText
from app.core.utils import SetupRow
from app.forms.features.feature import Panel
from app.translator import Slash
from app.ui.buttons import ButtonCopy
from app.ui.embed import Announcement, Card, ErrorBlock, Response
from app.ui.records import Records

Messages = Open[CopyText | Open[CopyText]]
"""Texts no primitive draws, each read by its owner's own code: a plain chat line, a
badge, or a table keyed by a closed vocabulary in code (one level, no deeper)."""


class CommandCopy(CopyBlock):
    """A command's `copy.yml`: one block per primitive that draws its texts, under keys
    relative to the command; `slash` is required for a command with cogs, `setup` and
    `stats` for one /setup offers."""

    slash: Slash | None = Field(
        None,
        description="The names and captions of the command's app commands.",
        json_schema_extra={"role": "structure"},
    )
    setup: SetupRow | None = Field(
        None,
        description="The command's row on /setup and on the first message.",
        json_schema_extra={"role": "structure"},
    )
    stats: Stats | None = Field(
        None,
        description="The command's statistics and its recent-activity line.",
        json_schema_extra={"role": "structure"},
    )
    panel: Panel | None = Field(
        None,
        description="What the command's feature adds to its manager panel.",
        json_schema_extra={"role": "structure"},
    )
    announcement: Announcement | None = Field(
        None,
        description="What the command posts in a server channel.",
        json_schema_extra={"role": "structure"},
    )
    errors: Open[ErrorBlock] = Field(
        default_factory=dict,
        description="The command's errors, each named `<key>.errors.<name>` by the "
        "code, a form or a validator.",
        json_schema_extra={"role": "structure"},
    )
    responses: Open[Response] = Field(
        default_factory=dict,
        description="The embeds the command answers with, each named "
        "`<key>.responses.<name>`.",
        json_schema_extra={"role": "structure"},
    )
    records: Open[Records] = Field(
        default_factory=dict,
        description="The command's browsable lists, each named `<key>.records.<name>`.",
        json_schema_extra={"role": "structure"},
    )
    buttons: Open[ButtonCopy] = Field(
        default_factory=dict,
        description="The command's own buttons, keyed by the aside a panel button "
        "runs or by what the button does.",
        json_schema_extra={"role": "structure"},
    )
    cards: Open[Card] = Field(
        default_factory=dict,
        description="The Components V2 cards the command draws, keyed by the screen.",
        json_schema_extra={"role": "structure"},
    )
    messages: Messages = Field(
        default_factory=dict,
        description="Texts no primitive draws, read by the command's own code.",
        json_schema_extra={"role": "structure"},
    )


class SharedCommands(CopyBlock):
    """`app/languages/commands.yml`: the screens the shared layers draw for every
    command, under `commands.`; their plain lines and tables live in `messages.yml`."""

    responses: Open[Response] = Field(
        default_factory=dict,
        description="The shared embeds and questions, each named "
        "`commands.responses.<name>` (the admin-only answer, the cooldown, the Help "
        "screen, the lifecycle final messages, the member pickers, the removal and "
        "discard questions).",
        json_schema_extra={"role": "structure"},
    )
    records: Open[Records] = Field(
        default_factory=dict,
        description="The shared lists, each named `commands.records.<name>` (a "
        "feature's History and its line per event, every change of a server).",
        json_schema_extra={"role": "structure"},
    )


class SharedButtons(RootModel[Open[ButtonCopy]]):
    """`app/languages/buttons.yml`: every button the shared layers draw, nothing but
    buttons."""


class SharedErrors(RootModel[Open[ErrorBlock]]):
    """`app/languages/errors.yml`: every error a shared layer names by its block,
    nothing but errors."""


class SharedMessages(RootModel[Messages]):
    """`app/languages/messages.yml`: the plain lines and tables the shared layers read
    (months, parent groups, notices, permission lines, placeholders, page and value
    words)."""


class LocaleNames(RootModel[Open[CopyText]]):
    """`app/languages/locales.yml`: the name of each language Keiko can speak, keyed by
    its language code."""


SHARED_FILES: Mapping[str, type[BaseModel]] = {
    "buttons": SharedButtons,
    "commands": SharedCommands,
    "errors": SharedErrors,
    "locales": LocaleNames,
    "messages": SharedMessages,
}
"""The schema of each shared copy file, by the stem its keys load under."""


def validated(document: Mapping[str, Any], prefix: str, shared: bool) -> BaseModel:
    """The copy file `document` loads under `prefix`, checked against its schema and
    against what its command declares."""
    if shared:
        if prefix not in SHARED_FILES:
            raise ValueError(f"no schema names a shared copy file {prefix}.yml")
        return SHARED_FILES[prefix].model_validate(document)
    copy = CommandCopy.model_validate(document)
    command = next((found for found in commands() if found.key == prefix), None)
    missing = _missing(command, copy) if command is not None else []
    if missing:
        raise ValueError(f"{prefix} writes no {', '.join(missing)}")
    return copy


def _missing(command: Command, copy: CommandCopy) -> list[str]:
    needed: list[tuple[str, BaseModel | None, bool]] = [
        ("slash", copy.slash, bool(command.cogs)),
        ("setup", copy.setup, command.on_setup),
        ("stats", copy.stats, command.on_setup),
    ]
    return [name for name, written, wanted in needed if wanted and written is None]
