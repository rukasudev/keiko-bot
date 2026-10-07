"""Every card section type the YAML may declare is one `SectionKind`."""

import typing

import pytest

from app.settings.form.actions.configuration_card import SECTION_KINDS
from app.settings.form.form_yaml import Section

pytestmark = pytest.mark.unit


def _declared_types():
    members = typing.get_args(typing.get_args(Section)[0])
    return {member.model_fields["type"].annotation.__args__[0] for member in members}


def test_every_declared_section_type_has_a_kind_and_no_kind_is_left_over():
    assert set(SECTION_KINDS) == _declared_types()


def test_only_sections_that_keep_a_default_tell_whether_they_were_customized():
    keeping_a_default = {
        name for name, kind in SECTION_KINDS.items() if kind.custom is not None
    }
    assert keeping_a_default == {"title-content", "file-upload"}
