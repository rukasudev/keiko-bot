"""Copy tokens: a `{plural:key|one|several}` reads its plural only past one value."""

import pytest

from app.settings.form.actions.action import Context, apply_tokens, description_of
from app.settings.form.form_state import Answer, Setup, new_session
from tests.forms.replay import NOW, ORIGIN, REGISTRY

pytestmark = pytest.mark.unit

PLURAL = "{plural:roles|Role to give|Roles to give}: {response:roles:role|Empty}"


def _session(answers, form="default_roles"):
    definition = REGISTRY.get(form)
    return new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers=answers,
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("202", "Role to give: <@&202>"),
        (["202"], "Role to give: <@&202>"),
        (["202", "203"], "Roles to give: <@&202>, <@&203>"),
        ([], "Role to give: Empty"),
        (None, "Role to give: Empty"),
    ],
)
def test_a_plural_token_reads_the_plural_only_for_more_than_one_value(raw, expected):
    """A3 (beta.2 round 4): Lucas wants the plural only when more than one
    value is chosen; none or one reads the singular."""
    answers = {} if raw is None else {"roles": Answer(raw)}

    assert apply_tokens(PLURAL, _session(answers), Context()) == expected


def test_a_plural_token_counts_the_card_draft_before_the_saved_answer():
    session = _session({"roles": Answer(["202", "203"])})

    drafted = apply_tokens(PLURAL, session, Context(), {"roles": "204"})

    assert drafted == "Role to give: <@&204>"


@pytest.mark.parametrize(
    "locale,members,bots",
    [
        (
            "pt-br",
            "- Cargo a ser atribuído a esses membros: <@&202>",
            "- Cargo a ser atribuído a esses bots: Em branco",
        ),
        (
            "en-us",
            "- Role to give these members: <@&202>",
            "- Role to give these bots: Empty",
        ),
    ],
)
def test_the_roles_step_names_one_role_and_no_role_in_the_singular(
    locale, members, bots
):
    """C2 (beta.2 round 3): each block of the sync step agrees with how many
    roles it gives, members and bots apart; round 4 reads no role singular."""
    definition = REGISTRY.get("default_roles")
    step = definition.step("sync_existing")
    session = _session({"default_roles": Answer("202")})
    context = Context(definition=definition, steps=definition.steps, locale=locale)

    described = description_of(step, session, context)

    assert members in described
    assert described.endswith(bots)


@pytest.mark.parametrize(
    "locale,line",
    [
        ("pt-br", "- Cargos a serem atribuídos a esses bots: <@&202>, <@&203>"),
        ("en-us", "- Roles to give these bots: <@&202>, <@&203>"),
    ],
)
def test_the_roles_step_names_several_roles_in_the_plural(locale, line):
    definition = REGISTRY.get("default_roles")
    step = definition.step("sync_existing")
    session = _session({"default_roles_bot": Answer(["202", "203"])})
    context = Context(definition=definition, steps=definition.steps, locale=locale)

    assert description_of(step, session, context).endswith(line)
