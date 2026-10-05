"""Each feature is declared once, and its form, its module and its command agree.

`Feature` in `app/constants.py` is the one table every feature list derives
from, except `COMMANDS_LIST`, which wave 3 derives: the key, the slash
command's group and namespace, the /setup row, what the feature needs and the
module that saves it. `SETUP_FEATURES`, `FEATURE_COMMANDS`, the moderation
defaults, `feature_keys` and the module `feature_for` imports come from it;
what cannot be derived must agree with it: the form YAML named after the key,
the module the spec names, the subgroup the cogs register under the group with
the namespace, and the copy.
"""

import os
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from app.constants import Commands, Feature, GuildConstants
from app.settings.features import feature_for, feature_keys
from app.settings.form.form_yaml import DiskSource

pytestmark = pytest.mark.unit

FEATURES_FOLDER = os.path.join("app", "settings", "features")
SHARED_MODULES = {"__init__", "feature", "subscriptions"}
LOCALES = ("en-us", "pt-br")


def _specs():
    return [feature.value for feature in Feature]


async def _registered_subgroups():
    from app.cogs import integrations, moderations, notifications

    bot = MagicMock()
    cogs = []
    bot.add_cog = AsyncMock(side_effect=cogs.append)
    for package in (moderations, notifications, integrations):
        await package.setup(bot)

    return {
        (cog.app_command.name, subgroup._locale_name.extras["namespace"])
        for cog in cogs
        for subgroup in cog.app_command.commands
    }


def test_every_form_on_disk_is_a_declared_feature_and_back():
    assert set(DiskSource().list()) == set(feature_keys())


def test_every_module_a_feature_names_exists_and_saves_that_feature():
    for spec in _specs():
        if spec.module is None:
            continue
        module = import_module(f"app.settings.features.{spec.module}")
        assert module.FEATURE.key == spec.key
        assert feature_for(spec.key) is module.FEATURE


def test_every_feature_module_is_named_by_a_declared_feature():
    modules = {
        filename.removesuffix(".py")
        for filename in os.listdir(FEATURES_FOLDER)
        if filename.endswith(".py")
    }
    named = {spec.module for spec in _specs() if spec.module is not None}
    assert modules - SHARED_MODULES == named


async def test_every_feature_command_is_registered_where_its_spec_says(deps):
    declared = {(spec.group, spec.namespace) for spec in _specs()}
    assert await _registered_subgroups() == declared


def test_the_lists_the_bot_reads_are_derived_from_the_declaration():
    keys = [spec.key for spec in _specs()]
    assert [entry["command_key"] for entry in Commands.SETUP_FEATURES] == keys
    assert list(Commands.FEATURE_COMMANDS) == keys
    defaults = GuildConstants.COGS_MODERATIONS_COMMANDS_DEFAULT
    assert {key for key in defaults if key != GuildConstants.IS_BOT_ONLINE} == set(keys)
    assert [
        entry["command_key"]
        for entry in Commands.SETUP_FEATURES
        if entry.get("answers_prefix")
    ] == [spec.key for spec in _specs() if spec.answers_prefix]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_feature_has_its_command_and_setup_copy(locale):
    def copy(folder):
        path = os.path.join("app", "languages", folder, f"{folder}.{locale}.yml")
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)[locale]

    commands, buttons = copy("commands"), copy("buttons")
    for spec in _specs():
        assert commands["groups"][spec.group]
        assert commands["commands"][spec.namespace]["name"]
        assert commands["commands"][spec.namespace]["subgroup"]
        assert buttons["setup"][spec.button_key]["label"]
        assert commands["commands"]["setup"]["embed"]["features"][spec.button_key]
