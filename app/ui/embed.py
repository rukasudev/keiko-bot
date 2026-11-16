"""Keiko's embeds and cards, and the copy blocks each one reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import discord
from pydantic import Field

from app.constants import KeikoIcons as icons
from app.constants import Style as constants
from app.core import i18n
from app.core.commands import Open, Text
from app.core.conditions import Override
from app.core.facts import Facts
from app.core.i18n import CopyBlock, CopyError, CopyText
from app.core.utils import fill, parse_locale


class FieldOverride(Override):
    """The value a stacked field draws while a rule holds, in place of its own."""

    value: CopyText | None = Field(
        None,
        description="The value drawn while the rule holds; `$name` marks a fact the "
        "code passes.",
        json_schema_extra={"role": "copy"},
    )


class ResponseOverride(Override):
    """What a response draws while a rule holds, in place of its own title or
    description."""

    title: CopyText | None = Field(
        None,
        description="The title drawn while the rule holds; absent keeps the block's.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The description drawn while the rule holds; absent keeps the "
        "block's.",
        json_schema_extra={"role": "copy"},
    )


class ResponseField(CopyBlock):
    """One stacked field of a response embed: its title, and its value when the copy
    writes it."""

    title: CopyText = Field(
        description="The field's name, drawn in bold above its value.",
        examples=[{"en-us": "Command", "pt-br": "Comando"}],
        json_schema_extra={"role": "copy"},
    )
    value: CopyText | None = Field(
        None,
        description="A value the copy writes itself, drawn whenever the code gives "
        "none (the how-to field of /support); absent, the code gives the value.",
        json_schema_extra={"role": "copy"},
    )
    empty: CopyText | None = Field(
        None,
        description="The value drawn while the code has none to give (a /report "
        "ticket without attachments); absent, such a field is left out.",
        json_schema_extra={"role": "copy"},
    )
    overrides: tuple[FieldOverride, ...] = Field(
        (),
        description="The value drawn while a rule on the facts holds, the first item "
        "whose rule holds winning (the link check's feature line in each state of "
        "the feature).",
        json_schema_extra={"role": "conditions"},
    )
    lines: Open[CopyText] = Field(
        default_factory=dict,
        description="Line templates the code picks from to compose the value, keyed "
        "by what each says (the reasons of the link check).",
        json_schema_extra={"role": "copy"},
    )


class Response(CopyBlock):
    """One embed Keiko answers with, drawn by `response_embed`: an answer, a
    `ConfirmActionView` question or the confirmation of a panel aside."""

    title: CopyText = Field(
        description="The embed's title, with one leading emoji; `$name` marks a value "
        "the code fills.",
        examples=[{"en-us": "🏓 Pong!", "pt-br": "🏓 Pong!"}],
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The embed's body; `$name` marks a value the code fills; absent, "
        "the embed has no body (the /report ticket, whose fields say it all).",
        examples=[
            {"en-us": "I answered in $ping ms.", "pt-br": "Respondi em $ping ms."}
        ],
        json_schema_extra={"role": "copy"},
    )
    footer: CopyText | None = Field(
        None,
        description="The closing subtext line: absent draws the standard /report "
        "line, null draws none, a text draws that text.",
        json_schema_extra={"role": "copy"},
    )
    empty: CopyText | None = Field(
        None,
        description="The body drawn instead of `description` while there is nothing "
        "to list (the /setup permissions report).",
        json_schema_extra={"role": "copy"},
    )
    overrides: tuple[ResponseOverride, ...] = Field(
        (),
        description="The title or description drawn while a rule on the facts holds, "
        "the first item whose rule holds winning (the link check, once it would block "
        "the message).",
        json_schema_extra={"role": "conditions"},
    )
    lines: Open[CopyText] = Field(
        default_factory=dict,
        description="Line templates the code repeats into the body, keyed by what "
        "each says (one line per category).",
        json_schema_extra={"role": "copy"},
    )
    fields: Open[ResponseField] = Field(
        default_factory=dict,
        description="The stacked fields below the body, drawn in the order written, "
        "keyed by the value the code gives each.",
        json_schema_extra={"role": "structure"},
    )


class ErrorBlock(CopyBlock):
    """One error, named by its block: a red embed through `response_error_embed`, or
    the plain line of the forms' `error_message`."""

    title: CopyText = Field(
        description="The error's title, without an emoji: the embed adds 🚨.",
        examples=[{"en-us": "Invalid Date", "pt-br": "Data Inválida"}],
        json_schema_extra={"role": "copy"},
    )
    message: CopyText = Field(
        description="What went wrong and how to recover; `$name` marks a value the "
        "code fills.",
        json_schema_extra={"role": "copy"},
    )


class AnnouncementDefaults(CopyBlock):
    """Keiko's own announcement, posted where the admin wrote none."""

    title: Text = Field(
        description="The announcement's title, an admin template: `{user}`, "
        "`{server}` and `{date}` are filled like in what the admin writes.",
        examples=[{"en-us": "🎉 Hooray, {user}!", "pt-br": "🎉 Viva, {user}!"}],
        json_schema_extra={"role": "copy"},
    )
    description: Text = Field(
        description="The announcement's body, an admin template like the title.",
        json_schema_extra={"role": "copy"},
    )


class Announcement(CopyBlock):
    """What a command posts in a server channel through `announcement_embed`: the
    labels around the content, and Keiko's own content where the admin wrote none."""

    fields: Open[CopyText] = Field(
        default_factory=dict,
        description="The label of each inline field, keyed by the value the code "
        "gives it (a live's game, a video's tags).",
        json_schema_extra={"role": "copy"},
    )
    states: Open[CopyText] = Field(
        default_factory=dict,
        description="The footer line of each state the announcement goes through "
        "(a live: online, offline, and its duration once over).",
        json_schema_extra={"role": "copy"},
    )
    samples: Open[CopyText] = Field(
        default_factory=dict,
        description="The sample a preview draws while there is no real content yet, "
        "keyed by the part it stands for (title, game, description).",
        json_schema_extra={"role": "copy"},
    )
    defaults: AnnouncementDefaults | None = Field(
        None,
        description="Keiko's own title and description, posted while the admin "
        "wrote none (a celebration).",
        json_schema_extra={"role": "structure"},
    )


class CardRow(CopyBlock):
    """One row of a card: a block of text with its control beside or under it."""

    title: CopyText | None = Field(
        None,
        description="The row's heading, drawn above its body (a setting's name).",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText = Field(
        description="The row's body; `$name` marks a value the code fills ($value, "
        "$prefix).",
        json_schema_extra={"role": "copy"},
    )
    placeholder: CopyText | None = Field(
        None,
        description="The placeholder of the select drawn under the row.",
        json_schema_extra={"role": "copy"},
    )


class CardOverride(Override):
    """What a card draws while a rule holds, in place of its own title, intro or
    note."""

    title: CopyText | None = Field(
        None,
        description="The title drawn while the rule holds; absent keeps the card's.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The intro drawn while the rule holds; absent keeps the card's.",
        json_schema_extra={"role": "copy"},
    )
    note: CopyText | None = Field(
        None,
        description="The note drawn while the rule holds; absent keeps the card's.",
        json_schema_extra={"role": "copy"},
    )


class Card(CopyBlock):
    """One Components V2 card a command draws through `card_header`, `card_row` and
    `card_footer`."""

    title: CopyText = Field(
        description="The card's `## title`, with one emoji.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText = Field(
        description="The intro under the title.",
        json_schema_extra={"role": "copy"},
    )
    note: CopyText | None = Field(
        None,
        description="The subtext line under the intro; `$name` marks a value the code "
        "fills ($page, $pages).",
        json_schema_extra={"role": "copy"},
    )
    footer: CopyText | None = Field(
        None,
        description="The card's closing subtext line: absent draws the standard "
        "/report line, null draws none, a text draws that text.",
        json_schema_extra={"role": "copy"},
    )
    overrides: tuple[CardOverride, ...] = Field(
        (),
        description="The title, intro or note drawn while a rule on the facts holds, "
        "the first item whose rule holds winning (the dashboard once everything is "
        "configured).",
        json_schema_extra={"role": "conditions"},
    )
    rows: Open[CardRow] = Field(
        default_factory=dict,
        description="The card's rows, keyed by what each row shows.",
        json_schema_extra={"role": "structure"},
    )


def standard_footer(locale: str | discord.Locale) -> str:
    """The standard /report line that closes Keiko's embeds and cards, with its
    bullet: the one text every form step also falls back to."""
    return i18n.standard_footer().get(parse_locale(locale))


def with_footer(embed: discord.Embed, footer: str) -> discord.Embed:
    """Close the embed with `footer` as its last subtext line, under its fields when
    it has some, where markdown renders as it never does in an embed footer."""
    if not footer:
        return embed

    line = f"-# {_bulleted(footer)}"
    if embed.fields:
        embed.add_field(name=constants.BLANK_FIELD_NAME, value=line, inline=False)
    else:
        embed.description = (
            f"{embed.description}\n\n{line}" if embed.description else line
        )
    return embed


def base_embed(
    title: str, description: str, thumbnail: str = icons.IMAGE_02, footer: str = ""
) -> discord.Embed:
    """Standard Keiko embed: background color, thumbnail, optional bullet footer."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=int(constants.BACKGROUND_COLOR, base=16),
    )
    embed.set_thumbnail(url=thumbnail)
    with_footer(embed, footer)
    return embed


def response_embed(
    multilang_key: str,
    locale: str | discord.Locale,
    color: int | discord.Colour | None = None,
    image: bool = False,
    *,
    facts: Facts | None = None,
    fields: Mapping[str, Any] | None = None,
    empty: bool = False,
    thumbnail: str = "",
    inline: bool = False,
) -> discord.Embed:
    """The embed of the Response block `multilang_key`: the overrides its `facts`
    choose in place, its `$name`s filled from them, its fields from `fields`, closed
    by the footer its copy decides."""
    copy, values = i18n.resolved(multilang_key, Response, facts)
    language = parse_locale(locale)
    body = _description(copy, empty, multilang_key)
    embed = discord.Embed(
        color=color or int(constants.BACKGROUND_COLOR, base=16),
        title=fill(copy.title.get(language), **values),
        description=fill(body.get(language), **values) if body else None,
    )
    _add_fields(embed, copy, fields or {}, language, values, inline, multilang_key)

    if thumbnail or image:
        embed.set_thumbnail(url=thumbnail or icons.IMAGE_01)
    with_footer(embed, _footer(copy.footer, "footer" in copy.model_fields_set, locale))
    return embed


def announcement_embed(
    title: str | None,
    message: str | None,
    footer: str | None = None,
    image: str | None = None,
    *,
    copy: str = "",
    locale: str | discord.Locale = "en-us",
    fields: Mapping[str, Any] | None = None,
    url: str | None = None,
    colour: int | discord.Colour | None = None,
    thumbnail: str = "",
) -> discord.Embed:
    """What a command posts in a server channel: the content, its picture and footer,
    and inline `fields` labelled by the Announcement block `copy`."""
    embed = discord.Embed(
        title=title,
        color=colour or int(constants.BACKGROUND_COLOR, base=16),
        description=message,
        url=url,
    )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if footer:
        embed.set_footer(text=f"• {footer}")
    if fields:
        labels = _announcement(copy).fields
        language = parse_locale(locale)
        for name, value in fields.items():
            if name not in labels:
                raise CopyError(f"{copy}.fields writes no {name}")
            if value not in (None, ""):
                embed.add_field(name=labels[name].get(language), value=value)

    embed.set_image(url=image)

    return embed


def response_error_embed(
    error_key: str,
    locale: str | discord.Locale,
    footer: bool = True,
    *,
    facts: Facts | None = None,
) -> discord.Embed:
    """The red embed of an error block: a command's `<key>.ui.errors.<name>`, or the
    name of a shared one; its `$name`s filled from `facts`."""
    named = error_key if "." in error_key else f"errors.{error_key}"
    block, values = i18n.resolved(named, ErrorBlock, facts)
    language = parse_locale(locale)

    embed = discord.Embed(
        color=int(constants.RED_COLOR, base=16),
        title=f"🚨 {fill(block.title.get(language), **values)}",
        description=fill(block.message.get(language), **values),
    )
    if footer:
        with_footer(embed, standard_footer(locale))

    return embed


@dataclass(frozen=True)
class CardCopy:
    """The texts of one Card block in one language: its overrides chosen, its `$name`s
    filled, and the closing line its footer decides."""

    title: str
    description: str
    note: str
    footer: str


def card_copy(
    key: str, locale: str | discord.Locale, facts: Facts | None = None
) -> CardCopy:
    """The texts of the Card block `key`, with the overrides `facts` choose and its
    `$name`s filled from them."""
    card, values = i18n.resolved(key, Card, facts)
    language = parse_locale(locale)
    note = fill(card.note.get(language), **values) if card.note else ""
    return CardCopy(
        title=fill(card.title.get(language), **values),
        description=fill(card.description.get(language), **values),
        note=note,
        footer=card_footer_text(card, locale),
    )


def card_frame(accent: discord.Colour | None = None) -> discord.ui.Container[Any]:
    """The frame of a card, in the house colour unless told otherwise."""
    colour = accent or discord.Colour(int(constants.BACKGROUND_COLOR, 16))
    return discord.ui.Container(accent_colour=colour)


def card_header(
    frame: discord.ui.Container[Any],
    title: str,
    intro: str = "",
    thumbnail: str = "",
    note: str = "",
) -> None:
    """The `## title` block, its intro and a subtext note, with a picture beside it."""
    lines = [f"## {title}"]
    if intro:
        lines.append(intro)
    if note:
        lines.append(f"-# {note}")
    if thumbnail:
        frame.add_item(
            discord.ui.Section(*lines, accessory=discord.ui.Thumbnail(thumbnail))
        )
        return
    for line in lines:
        frame.add_item(discord.ui.TextDisplay(line))


def card_row(
    frame: discord.ui.Container[Any],
    body: str,
    accessory: discord.ui.Button[Any] | None = None,
    separated: bool = True,
) -> None:
    """One block of text with its button beside it, under a separator by default."""
    if separated:
        frame.add_item(discord.ui.Separator())
    if accessory is None:
        frame.add_item(discord.ui.TextDisplay(body))
        return
    frame.add_item(discord.ui.Section(body, accessory=accessory))


def card_footer(frame: discord.ui.Container[Any], text: str) -> None:
    """A separator and the `-#` subtext line that closes a card."""
    if not frame.children or not isinstance(frame.children[-1], discord.ui.Separator):
        frame.add_item(discord.ui.Separator())
    frame.add_item(discord.ui.TextDisplay(f"-# {text}"))


def card_footer_text(card: Card, locale: str | discord.Locale) -> str:
    """The closing line a Card block decides, with its bullet: its own, the standard
    one, or none."""
    footer = _footer(card.footer, "footer" in card.model_fields_set, locale)
    return _bulleted(footer) if footer else ""


def _bulleted(line: str) -> str:
    return line if line.lstrip().startswith("•") else f"• {line}"


def _description(copy: Response, empty: bool, key: str) -> Text | None:
    if empty:
        if copy.empty is None:
            raise CopyError(f"{key} writes no empty")
        return copy.empty
    return copy.description


def _add_fields(
    embed: discord.Embed,
    copy: Response,
    given: Mapping[str, Any],
    language: str,
    filled: Mapping[str, Any],
    inline: bool,
    key: str,
) -> None:
    unknown = sorted(set(given) - set(copy.fields))
    if unknown:
        raise CopyError(f"{key}.fields writes no {', '.join(unknown)}")
    for name, field in copy.fields.items():
        value = given.get(name)
        if value not in (None, ""):
            shown = str(value)
        else:
            written = field.value if field.value is not None else field.empty
            if written is None:
                continue
            shown = fill(written.get(language), **filled)
        embed.add_field(name=field.title.get(language), value=shown, inline=inline)


def _footer(footer: Text | None, written: bool, locale: str | discord.Locale) -> str:
    if not written:
        return standard_footer(locale)
    return footer.get(parse_locale(locale)) if footer else ""


def _announcement(copy: str) -> Announcement:
    if not copy:
        raise CopyError("announcement fields need the Announcement block of a copy")
    return i18n.block(copy, Announcement)
