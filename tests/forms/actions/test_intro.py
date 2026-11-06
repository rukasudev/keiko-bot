"""The first screen lists every setting the form is about to ask for."""

import pytest

from app.settings.form.actions.intro import settings_list, settings_text
from app.settings.form.form_yaml import (
    CardStep,
    CompositionStep,
    DiskSource,
    compile_form,
)

pytestmark = pytest.mark.unit

FORMS = (
    "block_links",
    "default_roles",
    "notifications_twitch",
    "notifications_youtube_video",
    "reminders_birthday",
    "stream_elements_commands",
    "welcome_messages",
)


def _walked_cards(steps):
    """The cards the intro reads: top level and unconditioned setup lists."""
    for step in steps:
        if step.hidden or not step.in_setup:
            continue
        if isinstance(step, CompositionStep):
            if step.when is None:
                yield from _walked_cards(step.steps)
            continue
        if isinstance(step, CardStep):
            yield step


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("locale", ("pt-br", "en-us"))
def test_the_intro_lists_every_field_of_the_cards_it_reads(form, locale):
    """Broke as: welcome, Twitch and YouTube showed an empty settings list
    after their steps became one card, because a card field is only listed
    when it declares a description."""
    definition = compile_form(form, DiskSource().load(form))
    listed = settings_list(definition.steps, locale)

    for card in _walked_cards(definition.steps):
        for field in card.fields:
            if field.hidden:
                continue
            assert field.label.get(locale) in listed, (form, card.key, field.key)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("locale", ("pt-br", "en-us"))
def test_the_intro_gives_each_setting_one_line(form, locale):
    """Broke as: default roles listed its whole step description, numbered lines
    included, under one setting of the first screen, because the intro read every
    non-card step's full description."""
    definition = compile_form(form, DiskSource().load(form))

    for title, line in settings_list(definition.steps, locale).items():
        assert "\n" not in line, (form, title, line)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("locale", ("pt-br", "en-us"))
def test_a_setting_titled_as_a_question_takes_no_colon_after_it(form, locale):
    """Broke as: default roles' intro read "Dar os cargos para quem já está no
    servidor?: Se você quiser…", the question mark followed by the colon every
    setting got; the review's lines already drop it (`labelled`)."""
    definition = compile_form(form, DiskSource().load(form))

    text = settings_text(definition.steps, locale)

    assert "?:" not in text and "?**:" not in text, (form, text)
