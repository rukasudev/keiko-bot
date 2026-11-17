"""Form tokens: a `${plural.key|one|many}` reads its plural only past one value, and
an answer put in a text is never read again."""

import pytest

from app.forms.form.actions.action import Context, apply_tokens, description_of
from app.forms.form.form_state import Answer, Setup, new_session
from tests.forms.replay import NOW, ORIGIN, REGISTRY

pytestmark = pytest.mark.unit

PLURAL = "${plural.roles|Role to give|Roles to give}: ${answer.roles:role|Empty}"


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


def test_the_server_settings_are_context_tokens():
    context = Context(
        server_language="pt-br",
        server_timezone="America/Sao_Paulo",
    )
    body = (
        "${context.server_language} ${context.server_timezone} "
        "${context.server_mention}"
    )

    filled = apply_tokens(body, _session({}), context)

    assert filled == "pt-br America/Sao_Paulo ", "the mention is no setting any more"


def test_a_token_formats_its_value_by_the_format_names_of_the_form():
    """`format` values are snake_case in the form; a token's format reads the
    same names and formats as the saved style it maps to."""
    from app.forms.form.responses.styles import format_value

    session = _session({"date": Answer("05-12")})
    filled = apply_tokens("${answer.date:month_day}", session, Context())

    assert filled == format_value("05-12", "mm_dd", "en-us")
    assert filled != "05-12"


@pytest.mark.parametrize(
    "typed",
    ["evil${context.prefix}x", "$count of $$5", "{context:prefix}", "${random.1-10}"],
)
def test_an_answer_put_in_a_text_is_never_read_again(typed):
    """Broke as: `apply_tokens` filled the answers first and the context after, over
    the whole text, so an answer an admin typed as `evil{context:prefix}x` showed
    `evil!x`. Shared behaviour: the one pass of `app.core.tokens.resolved` every
    form text is filled by. Exposed by: the probes of the YAML syntax study.
    Guaranteed: what an admin typed shows as typed, whatever Keiko's syntax it
    looks like."""
    session = _session({"streamer": Answer(typed)})
    context = Context(prefix="!", server_name="Keiko")

    shown = apply_tokens(
        "${answer.streamer} on ${context.server_name}", session, context
    )

    assert shown == f"{typed} on Keiko"


def test_a_doubled_dollar_is_one_dollar_once_the_text_is_filled():
    session = _session({"streamer": Answer("shroud")})

    assert apply_tokens("$$5 for ${answer.streamer}", session, Context()) == (
        "$5 for shroud"
    )
