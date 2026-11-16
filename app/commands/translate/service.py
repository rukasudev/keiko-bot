"""The embed /translate-message answers with."""

from typing import Any

import discord

from app.core.facts import Facts, facts
from app.integrations.google_translate import Translation
from app.ui.embed import response_embed


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("translate", **values)


def translate_embed(
    locale: str | discord.Locale, response: Translation, message_url: str
) -> discord.Embed:
    """The translation of the message at `message_url`, in `locale`."""
    return response_embed(
        "translate.ui.responses.translation",
        locale,
        image=True,
        facts=command_facts(
            message_url=message_url,
            src_lang=response["src"],
            dest_lang=response["dest"],
            original_message=response["original"],
            translated_message=response["translated_message"],
        ),
    )
