"""/admin cogs offers every name it can load, read from the registry when asked."""

from unittest.mock import MagicMock

import pytest

from app.commands.admin.groups.cogs import Cogs, extension_choices
from app.constants import CogsConstants
from app.core.commands import commands
from app.core.utils import command_extensions, extension_names, extension_paths

pytestmark = pytest.mark.unit


async def test_the_choices_are_the_core_cogs_then_every_command_with_an_extension():
    offered = [choice.value for choice in await extension_choices(None, "")]

    extensions = [command.key for command in commands() if command_extensions(command)]
    assert offered == [*CogsConstants.CORE_COGS, *extensions]
    assert {
        "groups",
        "monitoring",
        "admin",
        "config",
        "events",
        "errors",
        "help",
    } <= set(offered), "the names an operator already types stay offered"


async def test_the_choices_narrow_to_what_the_admin_typed():
    offered = [choice.name for choice in await extension_choices(None, "MONI")]

    assert offered == ["monitoring"]


def test_every_offered_name_resolves_to_its_modules():
    assert all(extension_paths(name) for name in extension_names())
    assert "default_roles" not in extension_names(), "a subgroup reloads with groups"


def test_load_unload_and_reload_complete_the_cog_from_the_registry():
    group = Cogs(MagicMock())
    completed = {
        name: [param.autocomplete for param in group.get_command(name).parameters]
        for name in ("load", "unload", "reload")
    }

    assert completed == dict.fromkeys(("load", "unload", "reload"), [True])
