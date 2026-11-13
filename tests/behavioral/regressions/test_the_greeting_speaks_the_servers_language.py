"""The greeting a new server gets is written in the server's language.

Broke as: in beta.2 every greeting came out in English. The greeting compared the
server's locale, lowercased to `pt-br`, with `supported_locales`, which spells it
`pt-BR`, so a Brazilian server never matched and fell back to `en-us`. Shared
behaviour: the language a new server's settings get from the join's guess
(`guess_server_settings`), which every server-facing message reads
(`server_language`). Consumer that exposed it: the onboarding greeting.
Guaranteed: a server whose preferred locale is Brazilian Portuguese reads the
greeting, its buttons and its language select in pt-br, and any other server keeps
English.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.core.servers import guess_server_settings
from app.core.utils import ml
from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def _guild(db, preferred_locale):
    channel = SimpleNamespace(
        permissions_for=lambda _member: SimpleNamespace(send_messages=True),
        send=AsyncMock(),
    )
    guild = SimpleNamespace(
        id=987,
        preferred_locale=preferred_locale,
        text_channels=[channel],
        me=SimpleNamespace(),
    )
    recorded_server(db, guild.id, **guess_server_settings(guild))
    return guild, channel


GREETING = {
    "pt-br": {
        "buttons": ["Configurar", "Ajuda"],
        "language": "🇧🇷 Português",
        "category": "🔔 **Notificações:** Twitch Lives, YouTube Vídeos e Lembretes de Aniversário.",
    },
    "en-us": {
        "buttons": ["Setup", "Help"],
        "language": "🇺🇸 English",
        "category": "🔔 **Notifications:** Twitch Lives, YouTube Videos and Birthday Reminders.",
    },
}


@pytest.mark.parametrize(
    "preferred_locale,locale",
    [
        (discord.Locale.brazil_portuguese, "pt-br"),
        (discord.Locale.american_english, "en-us"),
        (discord.Locale.japanese, "en-us"),
    ],
)
async def test_the_greeting_is_sent_in_the_servers_language(deps, preferred_locale, locale):
    from app.views.greetings import GreetingsView

    guild, channel = _guild(deps.mongo_client, preferred_locale)

    await GreetingsView().send(guild)

    sent = channel.send.await_args.kwargs
    assert sent["embed"].title == ml("messages.greetings-message.title", locale)
    assert f"\n{GREETING[locale]['category']}\n" in sent["embed"].description
    view = sent["view"]
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
    assert [button.label for button in buttons] == GREETING[locale]["buttons"]
    assert len(selects) == 1
    assert selects[0].placeholder == GREETING[locale]["language"]
