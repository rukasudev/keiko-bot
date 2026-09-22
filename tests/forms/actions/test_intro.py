"""The first screen lists every setting the form is about to ask for."""

import pytest

from app.settings.form.actions.intro import settings_list
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
    """The cards the intro reads: top level and unconditioned compositions."""
    for step in steps:
        if step.hidden:
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
