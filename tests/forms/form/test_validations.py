"""A validator and a transform read the keys and services their YAML declares."""

import pytest

from app.forms.form.responses.transforms import transform
from app.forms.form.responses.validations import ValidationContext, validator

pytestmark = pytest.mark.unit

YOUTUBE = {
    "item": "youtuber",
    "service": "youtube",
    "id": "channel_id",
    "error": "youtuber",
}


def _registered(value, items=(), external=None, params=YOUTUBE):
    check = validator("registered_and_found")
    context = ValidationContext(items=items, external=external or {}, params=params)
    return check.check(value, context)


def test_a_registered_name_is_refused_under_the_declared_prefix():
    saved = [{"youtuber": {"value": "PewDiePie"}}]

    assert _registered("pewdiepie", items=saved) == "youtuber_already_registered"


def test_a_name_the_declared_service_cannot_find_is_refused():
    assert _registered("ghost", external={"youtube": {}}) == "youtuber_not_found"
    assert _registered("ghost", external={"twitch": {"channel_id": "1"}}) == (
        "youtuber_not_found"
    )


def test_a_prefix_that_names_a_command_block_is_refused_in_snake_case():
    params = {**YOUTUBE, "error": "notifications_youtube_video.ui.errors.youtuber"}
    saved = [{"youtuber": {"value": "PewDiePie"}}]

    assert _registered("pewdiepie", items=saved, params=params) == (
        "notifications_youtube_video.ui.errors.youtuber_already_registered"
    )
    assert _registered("ghost", external={"youtube": {}}, params=params) == (
        "notifications_youtube_video.ui.errors.youtuber_not_found"
    )


def test_a_new_name_the_service_found_passes():
    assert _registered("shroud", external={"youtube": {"channel_id": "UC1"}}) is None


def test_the_needs_name_the_declared_service():
    check = validator("registered_and_found")

    assert check.needed(YOUTUBE) == ("external:youtube", "items")


def test_the_date_reads_its_month_from_the_declared_key():
    check = validator("date")
    context = ValidationContext(
        answers={"birth_month": "02"}, params={"month": "birth_month"}
    )

    assert check.check("31", context) == "invalid_date"
    assert check.check("28", context) is None


def test_the_date_is_read_from_the_day_and_month_and_seeds_them_back():
    rule = transform("mm_dd_date_parts")

    assert rule.read({"day": "5", "month": "maio"}) == "05-05"
    assert rule.split("12-01") == {"month": "12", "day": "1"}
