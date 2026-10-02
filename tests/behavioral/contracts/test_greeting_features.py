"""The greeting a new server gets offers every feature /setup offers.

The card kept a list of its own while `/setup` read `Commands.SETUP_FEATURES`,
so a new server was told about four features and the dashboard about seven.
Reading the one registry is the fix, and the view has to survive it: Discord
fits five components to a row, so a hardcoded row breaks the moment the
registry grows past it.

Shared behaviour: `Commands.SETUP_FEATURES`, read by the greeting and by the
setup dashboard. Consumer that exposed it: the onboarding greeting, which
raised on construction and left a new server with no greeting at all and
`on_ready` half finished. Guaranteed: the greeting is buildable, in both
locales, with one button per feature.
"""

import discord
import pytest

from app.constants import Commands
from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_features")]


def _greeting(locale):
    from app.views.greetings import GreetingsView

    return GreetingsView(locale)


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_greeting_offers_every_setup_feature(locale):
    view = _greeting(locale)
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]

    assert len(buttons) == len(Commands.SETUP_FEATURES) + 1, (
        "one button per feature, plus the dashboard"
    )
    labels = {button.label for button in buttons}
    for feature in Commands.SETUP_FEATURES:
        expected = ml(f"buttons.setup.{feature['button_key']}.label", locale=locale)
        assert expected in labels, (feature["button_key"], sorted(labels))


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_greeting_fits_discord_rows(locale):
    """Broke as: every button was pinned to row 0, which fits five."""
    view = _greeting(locale)

    rows = [item.row for item in view.children]
    assert len(view.children) <= 25, "Discord fits five rows of five"
    assert max(row for row in rows if row is not None) <= 4, rows
