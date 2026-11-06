"""An optional select on a card can be emptied again.

Broke as: the exempt channels and roles of block links sit on one picker
(the `selects` card section), and every select it drew asked for at least
one value (`min_values=1`), because the renderer read "several values" as
"at least one". An admin who had freed a channel could never free none again.

Shared behaviour: the native selects the renderer draws for a `Picker`
(`app/settings/discord/views.py`, `_select_item`) and the pickers of the
`selects` section (`app/settings/form/actions/configuration_card.py`).
Exposed by block links' "Canais e cargos liberados".

Guaranteed: a select whose field the card does not require takes zero
values, and emptying it saves the field empty; a required one still asks
for one.
"""
import discord
import pytest

from tests.behavioral.harness.locators import walk_items

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


def _selects(scenario):
    return [
        item
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, (discord.ui.ChannelSelect, discord.ui.RoleSelect))
    ]


async def test_the_exempt_channels_can_be_emptied_again(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")           # Configurar the exceptions
    await scenario.select_option("general", target="allowed_chats")

    assert [select.min_values for select in _selects(scenario)] == [0, 0]
    await scenario.select_option([], target="allowed_chats")

    assert scenario.card_state.get("allowed_chats") in ([], None)
    await scenario.finish()
