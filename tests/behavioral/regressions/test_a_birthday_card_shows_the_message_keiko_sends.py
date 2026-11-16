"""The default message a birthday card shows is the one the bot posts.

Broke as: the message section of the birthday settings card drew its own default
from `birthdays/form.yml` ("🎂 Happy Birthday {user}!", "May your day be full of joy
and treats! 🎉✨"), and its modal started from it, while a celebration posted the
copy's defaults ("🎂 It's {user}'s birthday!", "Happy birthday {user}! 🎉 Hope your
day is full of joy and treats! ✨"): an admin who kept the default saw one text and
the members got another.

Shared behaviour: the default of a title-content section (`TitleContentSection`,
drawn by `_title_content_preview` and opened by `_title_content_modal` in
`app/forms/form/actions/configuration_card.py`) and the celebration's own message
(`message_source` in `app/commands/birthdays/service.py`). Consumer that exposed it:
reminders_birthday (the YAML audit of the form fields).

Guaranteed: while nobody customized it, the settings card draws, and its modal starts
from, the title and the message a celebration posts, in both languages.
"""

from collections import Counter

import pytest

from app.commands.birthdays.celebration import celebrate_in_guild
from app.commands.birthdays.service import save_birthday_config
from app.core.servers import set_feature_enabled
from tests.behavioral.harness.locators import walk_items
from tests.generators.birthdays import saved_birthday
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

MEMBER = "Tester"
CUSTOMIZE = {"pt-br": "Personalizar mensagem", "en-us": "Customize message"}


async def _card_default(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start("reminders_birthday")
    await scenario.confirm()
    scenario.expect_step("birthday_config")
    lines = [
        line
        for item in walk_items(scenario.current_message.view.children[0])
        for line in (getattr(item, "content", "") or "").splitlines()
        if line.startswith("> ")
    ]
    title = next(line for line in lines if line.startswith("> **"))
    message = lines[lines.index(title) + 1]
    await scenario.click(CUSTOMIZE[locale])
    started = [field.default for field in scenario.store.pending_modal.children]
    return (title[len("> **") : -len("**")], message[len("> ") :]), started


async def _posted(deps, locale):
    guild = deps.guild
    recorded_server(deps.mongo_client, guild.id, language=locale)
    member = create_member(guild, id=555, name=MEMBER)
    channel = guild.text_channels[0]
    await save_birthday_config(str(guild.id), str(channel.id), False)
    saved_birthday(str(guild.id), "555", "05-12", reminder_id="4001")
    await set_feature_enabled(str(guild.id), "reminders_birthday", True)

    await celebrate_in_guild(
        str(guild.id), [{"user_id": "555", "date": "05-12"}], "05-12", Counter()
    )
    embed = channel.get_last_embed()
    return member, embed.title, embed.description


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_card_draws_and_starts_from_the_message_a_celebration_posts(
    scenario_factory, deps, locale
):
    (title, message), started = await _card_default(scenario_factory, locale)
    member, posted_title, posted_message = await _posted(deps, locale)

    assert title.replace("{user}", MEMBER) == posted_title
    assert message.replace("{user}", member.mention) == posted_message
    assert started == [title, message]
