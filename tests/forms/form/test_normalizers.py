"""A normalizer that declares a response tells it once whenever it changed a text."""

import pytest

from app.forms.form.actions import modal
from app.forms.form.form_yaml import Extension, TextStep
from app.forms.form.responses.transforms import (
    NORMALIZERS,
    normalizer,
    told,
    without_group_mentions,
)

pytestmark = pytest.mark.unit


def test_every_registered_normalizer_names_a_response_of_the_copy_or_none():
    from app.core.utils import ml

    for name, rule in NORMALIZERS.items():
        if rule.announce:
            assert ml(f"commands.responses.{rule.announce}.title", locale="pt-br"), name


def test_a_normalizer_tells_its_response_only_for_a_text_it_changed():
    names = ["without_group_mentions", "without_group_mentions", None]

    assert told(names, ["{streamer} live", "a", "@everyone"]) == ()
    assert told(names, ["@everyone live", "<@&1> b", "c"]) == ("mention_removed",)


def test_a_normalizer_without_a_response_tells_nothing():
    assert normalizer("lowercase").apply("YouTube.com") == "youtube.com"
    assert told(["lowercase"], ["YouTube.com"]) == ()


def test_a_text_step_tells_what_its_normalizer_took_out():
    step = TextStep.model_validate(
        {
            "type": "text",
            "key": "message",
            "title": {"en-us": "Message", "pt-br": "Mensagem"},
            "description": {"en-us": "Type it", "pt-br": "Digite"},
            "normalize": Extension(name="without_group_mentions"),
        }
    )

    assert modal.told(step, {"inputs": ["@here hi"]}) == ("mention_removed",)
    assert modal.told(step, {"inputs": ["hi"]}) == ()


@pytest.mark.parametrize(
    "typed, kept",
    [
        ("@everyone gaules is live!", "gaules is live!"),
        ("gaules is live! @here", "gaules is live!"),
        ("Hi <@&123>, gaules is live", "Hi, gaules is live"),
        ("<@123> and <@!456>, gaules is live", "<@123> and <@!456>, gaules is live"),
        (
            "@here\n{streamer} @everyone live\nno mention",
            "\n{streamer} live\nno mention",
        ),
        ("@everyone@here", ""),
        ("Vem ver a live com @heretic_jogos!", "Vem ver a live com @heretic_jogos!"),
        ("Veja @heresy agora", "Veja @heresy agora"),
        ("contato@everyone.com", "contato@everyone.com"),
    ],
    ids=[
        "everyone",
        "here",
        "role",
        "members-stay",
        "per-line",
        "only-mentions",
        "a-handle-that-starts-with-here",
        "a-word-that-starts-with-here",
        "an-address-that-holds-everyone",
    ],
)
def test_a_typed_group_mention_is_taken_out_of_the_text(typed, kept):
    """Who a notice pings is its Mention field: an @everyone, @here or role typed in
    the text goes, with the space before it; a member's mention, which the field
    cannot name, stays as typed."""
    assert without_group_mentions(typed) == kept


@pytest.mark.parametrize(
    "typed",
    ["Vem ver a live com @heretic_jogos!", "Veja @heresy", "contato@everyone.com"],
)
def test_a_word_that_only_starts_like_a_mention_tells_nothing(typed):
    """A handle, a word or an address that holds @here or @everyone is no mention:
    it stays as typed, so nothing is told."""
    assert told(["without_group_mentions"], [typed]) == ()
