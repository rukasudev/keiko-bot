"""The embed /translate-message answers with."""

import discord

from app.integrations.google_translate import Translation
from app.ui.embed import response_embed


def translate_embed(
    locale: str | discord.Locale, response: Translation, message_url: str
) -> discord.Embed:
    """The translation of the message at `message_url`, in `locale`."""
    return response_embed(
        "translate.responses.translation",
        locale,
        image=True,
        values={
            "message_url": message_url,
            "src_lang": response["src"],
            "dest_lang": response["dest"],
            "original_message": response["original"],
            "translated_message": response["translated_message"],
        },
    )
