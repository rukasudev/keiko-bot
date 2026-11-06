"""The greeting a new server gets is written in the server's language.

Broke as: in beta.2 every greeting came out in English. The greeting compared the
server's locale, lowercased to `pt-br`, with `supported_locales`, which spells it
`pt-BR`, so a Brazilian server never matched and fell back to `en-us`. Shared
behaviour: the locale every server-facing message picks (`guild_locale`).
Consumer that exposed it: the onboarding greeting. Guaranteed: a server whose
preferred locale is Brazilian Portuguese reads the greeting, its buttons and its
language select in pt-br, and any other server keeps English.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def _guild(preferred_locale):
    channel = SimpleNamespace(
        permissions_for=lambda _member: SimpleNamespace(send_messages=True),
        send=AsyncMock(),
    )
    return SimpleNamespace(
        id=987,
        preferred_locale=preferred_locale,
        text_channels=[channel],
        me=SimpleNamespace(),
    ), channel


GREETING = {
    "pt-br": {"buttons": ["Configurar", "Comandos"], "language": "🇧🇷 Português"},
    "en-us": {"buttons": ["Setup", "Commands"], "language": "🇺🇸 English"},
}


@pytest.mark.parametrize(
    "preferred_locale,locale",
    [
        (discord.Locale.brazil_portuguese, "pt-br"),
        (discord.Locale.american_english, "en-us"),
        (discord.Locale.japanese, "en-us"),
    ],
)
async def test_the_greeting_is_sent_in_the_servers_language(preferred_locale, locale):
    from app.views.greetings import GreetingsView

    guild, channel = _guild(preferred_locale)

    await GreetingsView().send(guild)

    sent = channel.send.await_args.kwargs
    assert sent["embed"].title == ml("messages.greetings-message.title", locale)
    view = sent["view"]
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
    assert [button.label for button in buttons] == GREETING[locale]["buttons"]
    assert len(selects) == 1
    assert selects[0].placeholder == GREETING[locale]["language"]
