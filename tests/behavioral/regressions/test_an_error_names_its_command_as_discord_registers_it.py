"""The default roles error names the command a member can type, in each language.

Broke as: pressing Sync on the default roles panel once the feature was off answered
"To activate and configure this feature, just run the `/default roles` command" (pt-br
"`/cargos padrão`"), a command Discord does not have: the command is the subcommand
`/moderations default roles` (`/moderações cargos padrão`), and the text typed its
last two names in. Shared behaviour: a slash command a text names, written as its
context entry `{context:commands.<key>}` and filled by the copy loader (`with_context`
in `app/core/facts.py`) from the command's own `slash` copy under its parent group.
Consumer that exposed it: `set_on_default_roles_sync` (`app/commands/roles/service.py`),
the error `default_roles.ui.errors.deactivated`. Guaranteed: the error names exactly
the path the synced tree registers for the default roles command, in the reader's
language; every other text is held by
`tests/behavioral/contracts/test_a_text_names_only_registered_commands.py`.
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.commands import command_for
from tests.helpers.slash_tree import synced_tree

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

NAMED = re.compile(r"`(/[^`]+)`")


@pytest.fixture
async def tree():
    return await synced_tree()


@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
async def test_the_roles_sync_of_a_server_without_the_feature_names_its_real_command(
    mock_cache, tree, locale
):
    from app.commands.roles import service

    mock_cache.return_value = None
    interaction = MagicMock(guild=MagicMock(id=10), locale=locale)
    interaction.followup.send = AsyncMock()

    await service.set_on_default_roles_sync(interaction)

    description = interaction.followup.send.await_args.kwargs["embed"].description
    message, _closing = description.split("\n-# ", 1)
    registered = tree.path_of(command_for("default_roles").cogs, locale)
    assert len(registered) == 1
    assert NAMED.findall(message) == registered
