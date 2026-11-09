"""Contract: every setup ends with the activation embed and then its panel.

Every form's Confirmar replaces the summary by the closed "🎉 Funcionalidade
ativada com sucesso!" embed, with no controls left, which stays in the chat as
the record of the activation, and then sends the panel the command opens as a
new message right below it (round 7 J3). The panel works: its first Edit or Add
opens what it names. The panel adds nothing to what the activation records:
the same product events, the same story and the same audit record as a setup
that sent no panel.
"""
import pytest

from app.services.utils import ml
from app.settings.discord.callbacks import Runtime
from app.settings.form.form_state import Manage
from app.settings.form.form_yaml import DiskSource
from tests.behavioral.golden.paths import all_paths
from tests.behavioral.harness import locators
from tests.mocks.discord import create_guild

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

SETUPS = [
    pytest.param(path, locale, id=f"{path.form}.{locale}")
    for path in all_paths()
    if path.name == "setup_happy"
    for locale in path.locales
]

ONE_LOCALE = [
    pytest.param(path, id=path.form)
    for path in all_paths()
    if path.name == "setup_happy"
]

VOLATILE = ("duration_ms", "duration_bucket", "ms_on_step")



def _drawn(scenario):
    return [
        event
        for event in scenario.outputs
        if event.get("actor") == "bot"
        and (event.get("embed") or event.get("components_v2"))
    ]


def _activation_embeds(scenario, locale):
    title = ml("commands.command-events.enabled.title", locale=locale)
    return [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]


def _first_opener(message):
    for item in locators.clickable_items(message):
        target = locators.codec_target(item) or ""
        if target.split(":", 1)[0] in ("edit", "add") and ":" in target:
            return target
    raise AssertionError("the panel has no Edit or Add")


def _in_guild(scenario_factory, guild_id):
    def factory(locale="pt-br", guild=None, user=None):
        guild = guild or create_guild()
        guild.id = guild_id
        return scenario_factory(locale=locale, guild=guild, user=user)

    return factory


def _records(guild_id, form, analytics_events, stories, deps):
    events = [
        (
            event["event"],
            event["source"],
            {
                key: value
                for key, value in event["props"].items()
                if key not in VOLATILE
            },
        )
        for event in analytics_events
        if event["guild_id"] == guild_id
    ]
    told = [story[1:] for story in stories if story[0] == guild_id]
    audit = [
        {
            key: value
            for key, value in record.items()
            if key not in ("datetime", "guild_id")
        }
        for record in deps.mongo_client.events[form].find(
            {"guild_id": guild_id}, {"_id": False}
        )
    ]
    return events, told, audit


def test_every_form_has_a_setup_to_check():
    forms = {path.values[0].form for path in ONE_LOCALE}

    assert forms == set(DiskSource().list())


@pytest.mark.parametrize("path,locale", SETUPS)
async def test_a_setup_ends_with_the_activation_embed_and_then_its_panel(
    path, locale, scenario_factory, deps
):
    scenario = await path.run(scenario_factory, deps, locale)
    await scenario.finish()

    *_, activation, panel = _drawn(scenario)
    title = ml("commands.command-events.enabled.title", locale=locale)
    assert (activation.get("embed") or {}).get("title") == title
    assert not activation.get("components"), "the closed message keeps no controls"
    assert panel["components_v2"] and panel["kind"] == "followup_send"
    assert panel["message"] != activation["message"], "the panel is a new message"
    assert panel["seq"] > activation["seq"], "the panel comes right below it"
    assert isinstance(scenario.session.mode, Manage)


@pytest.mark.parametrize("path", ONE_LOCALE)
async def test_the_panel_after_a_setup_opens_what_its_first_edit_or_add_names(
    path, scenario_factory, deps
):
    scenario = await path.run(scenario_factory, deps, "pt-br")
    panel = scenario.current_message
    drawn = len(_drawn(scenario))

    await scenario.click(_first_opener(panel))

    opened = scenario.store.pending_modal is not None or len(_drawn(scenario)) > drawn
    assert opened, "the click opens a card or a modal"
    assert not [
        event
        for event in scenario.outputs[-3:]
        if (event.get("embed") or {}).get("color") == 0xFF0000
    ], "the click answers no error"
    await scenario.finish()


@pytest.mark.parametrize("path", ONE_LOCALE)
async def test_the_activation_records_the_same_with_or_without_the_panel(
    path, scenario_factory, deps, analytics_events, stories, monkeypatch
):
    """The panel after the setup adds no event, no story and no audit record."""
    form = path.form
    panel_run = await path.run(_in_guild(scenario_factory, 1001), deps, "pt-br")

    async def no_panel(self, interaction, session):
        return None

    monkeypatch.setattr(Runtime, "_open_panel", no_panel)
    embed_run = await path.run(_in_guild(scenario_factory, 1002), deps, "pt-br")
    monkeypatch.undo()

    shown = [event["embed"] for event in _activation_embeds(panel_run, "pt-br")]
    assert shown, "the setup closes with the activation embed"
    assert shown == [
        event["embed"] for event in _activation_embeds(embed_run, "pt-br")
    ]
    assert isinstance(panel_run.session.mode, Manage), "the panel was sent"
    assert _drawn(embed_run)[-1].get("embed"), "without it the embed is the last"
    on_panel = _records("1001", form, analytics_events, stories, deps)
    on_embed = _records("1002", form, analytics_events, stories, deps)
    assert on_panel[0] and on_panel[1] and on_panel[2]
    assert on_panel == on_embed
    await panel_run.finish()
    await embed_run.finish()
