"""Keiko speaks of himself in the masculine in every pt-br string.

Broke as: the welcome card said "eu já fico pronta", and the link check said "fui
configurada", "Estou pausada" and "Estou ativa", while every other screen calls
Keiko "o Keiko" and "animado". Shared behaviour: the pt-br language files, read by
every screen. Consumer that exposed it: the welcome messages setup card in the
beta.2 review. Guaranteed: no pt-br string puts a feminine adjective after a
verb in Keiko's first person.
"""
import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

LANGUAGES = Path(__file__).resolve().parents[3] / "app" / "languages"
FIRST_PERSON_FEMININE = re.compile(
    r"\b(?:estou|fico|fui|sou|estarei|ficarei|serei)\s+(?:\w+\s+)?"
    r"(?:pronta|ativa|sozinha|\w+ada)\b|\bobrigada\b",
    re.IGNORECASE,
)


def _pt_br_strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "en-us":
                continue
            yield from _pt_br_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _pt_br_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _files():
    yield from sorted(LANGUAGES.glob("*/*.pt-br.yml"))
    yield from sorted((LANGUAGES / "form").glob("*.yml"))


@pytest.mark.parametrize("path", list(_files()), ids=lambda path: path.name)
def test_no_pt_br_string_calls_keiko_feminine(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    found = [
        (where, match.group(0))
        for where, value in _pt_br_strings(document)
        for match in FIRST_PERSON_FEMININE.finditer(value)
    ]

    assert found == []
