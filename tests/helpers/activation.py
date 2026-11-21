"""What every form's setup records and ends with: the setups to walk, the
activation embed, a scenario on a server of a given id, and the events, story and
audit record a setup leaves, compared without what varies between runs."""
import pytest

from app.core.utils import ml
from tests.behavioral.golden.paths import all_paths
from tests.mocks.discord import create_guild

# Every form's happy setup, in each locale it is recorded in.
SETUPS = [
    pytest.param(path, locale, id=f"{path.form}.{locale}")
    for path in all_paths()
    if path.name == "setup_happy"
    for locale in path.locales
]

VOLATILE = ("duration_ms", "duration_bucket", "ms_on_step")


def activation_embeds(scenario, locale):
    """The activation embeds the scenario showed, in `locale`."""
    title = ml("commands.responses.enabled.title", locale=locale)
    return [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]


def in_guild(scenario_factory, guild_id):
    """A scenario factory whose server has the id `guild_id`."""
    def factory(locale="pt-br", guild=None, user=None):
        guild = guild or create_guild()
        guild.id = guild_id
        return scenario_factory(locale=locale, guild=guild, user=user)

    return factory


def _without_source(props):
    return {key: value for key, value in props.items() if key != "source"}


def records(guild_id, form, analytics_events, stories, deps):
    """The events, the story and the audit record `form`'s setup left on the server."""
    events = [
        (
            event["type"],
            event["props"].get("source"),
            {
                key: value
                for key, value in event["props"].items()
                if key not in VOLATILE and key != "source"
            },
        )
        for event in analytics_events
        if event["guild_id"] == guild_id
    ]
    told = [story[1:] for story in stories if story[0] == guild_id]
    audit = [
        {
            key: _without_source(value) if key == "props" else value
            for key, value in record.items()
            if key not in ("ts", "guild_id", "trace_id", "session_id")
        }
        for record in deps.mongo_client.events.changes.find(
            {"guild_id": guild_id, "cog": form}, {"_id": False}
        )
    ]
    return events, told, audit
