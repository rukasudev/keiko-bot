"""Regression: every list of a server's changes names a change by its feature.

What broke: the History of the admin log inspection, which lists every change of
a server, named each one by its feature's storage key ("➕ Item adicionado em
**block_links** por <@555>"), while /setup's History named the same change by the
feature's name ("🚫 Bloquear Links"). An admin and an operator read the same
record two ways, and the key is no name anyone sees in Discord.

Shared behavior affected: `parse_history_data` and the label it gives a change
(app/core/manager.py), read by the admin inspection
(app/commands/admin/log_inspection.py) and /setup's History
(app/commands/setup/service.py).

Exposed by: block_links, an item added from its panel.

Must remain guaranteed: both lists name a change by the emoji and the name /setup
gives its feature, and neither shows the key.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.behavioral.golden.paths.common import seed_history
from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

NAME = "🚫 Bloquear Links"


def _asking(guild):
    interaction = MockInteraction(user=create_member(guild), guild=guild, locale="pt-BR")
    interaction.client = SimpleNamespace(user=SimpleNamespace(id=999))
    interaction.edit_original_response = AsyncMock()
    return interaction


def _shown(interaction):
    view = next(sent["view"] for sent in interaction._responses if sent.get("view"))
    return "\n".join(f"{field.name}\n{field.value}" for field in view.embed.fields)


async def test_the_admin_inspection_names_a_change_by_its_feature(deps, guild):
    from app.commands.admin.log_inspection import LogInspectionView

    seed_history(deps, "block_links", "added")
    interaction = _asking(guild)

    await LogInspectionView(None, guild, str(guild.id)).history_callback(interaction)

    shown = _shown(interaction)
    assert NAME in shown
    assert "block_links" not in shown


async def test_the_setup_history_names_a_change_by_its_feature(deps, guild):
    from app.commands.setup.service import send_history

    seed_history(deps, "block_links", "added")
    interaction = _asking(guild)

    await send_history(interaction)

    shown = _shown(interaction)
    assert NAME in shown
    assert "block_links" not in shown
