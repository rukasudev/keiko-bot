"""Failures the production archive recorded, pinned so they cannot return.

Two years of daily log files were indexed and grouped by signature. These are
the ones whose cause still lived in the code. Each test names the count the
archive holds, because a regression here is not hypothetical: it already
happened that many times.
"""
import logging

import pytest

from app import logger as logger_module
from app.constants import DiscordLimits as limits
from app.constants import LogTypes as logconstants
from app.services import block_links

pytestmark = [pytest.mark.regression, pytest.mark.unit]


# --------------------------------------------------------------------------
# "In embeds.0.description: Must be 4096 or fewer in length." — 141 occurrences
# --------------------------------------------------------------------------

def make_record(message, level=logging.ERROR, **extra):
    record = logging.LogRecord(
        name="keiko", level=level, pathname="app/services/thing.py",
        lineno=1, msg=message, args=(), exc_info=None,
    )
    record.asctime = "2026-08-23 10:00:00"
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def handler():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    bot = SimpleNamespace(loop=MagicMock(), config=SimpleNamespace(
        ADMIN_LOGS_CHANNEL_ID=1, ADMIN_LOGS_ERROR_CHANNEL_ID=2,
        ADMIN_LOGS_COMMAND_CALL_ID=3, ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
    ), get_channel=lambda _id: None)

    instance = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(instance)
    return instance


def test_a_huge_log_message_does_not_blow_past_the_embed_limit(handler):
    """Discord rejects the whole message, so the log about the failure is the
    thing that fails to be logged."""
    embed = handler.add_embed(make_record(
        "x" * 9000, log_type=logconstants.COMMAND_ERROR_TYPE
    ))

    assert len(embed.description) <= limits.EMBED_DESCRIPTION


def test_an_enormous_traceback_does_not_blow_past_the_embed_limit(handler):
    """`format_traceback_message` caps the frames at 3000 characters, but the
    exception text and the file path are appended afterwards and were not
    capped at all."""
    try:
        raise ValueError("y" * 6000)
    except ValueError:
        import sys
        record = make_record("boom")
        record.exc_info = sys.exc_info()
        embed = handler.add_embed(record)

    assert len(embed.description) <= limits.EMBED_DESCRIPTION


def test_the_clipped_description_keeps_the_beginning(handler):
    embed = handler.add_embed(make_record(
        "the part that matters " + "x" * 9000,
        log_type=logconstants.COMMAND_ERROR_TYPE,
    ))

    assert embed.description.startswith("the part that matters")


# --------------------------------------------------------------------------
# "KeyError: 'allowed_chats'" — 210 occurrences, on every message
# --------------------------------------------------------------------------

def test_a_config_saved_before_the_field_existed_does_not_raise():
    """Broke as: `config[ALLOWED_CHATS_KEY]["values"]` on a document written
    before that field existed, inside on_message, so it fired on every message
    that guild received.

    The block-links redesign fixed it by normalizing first. This pins the
    guarantee rather than the current call order, because the subscript is
    still there and only the normalizer keeps it safe.
    """
    legacy = {"block_links": {"enabled": True}}

    normalized = block_links.normalize_block_links_config(legacy)

    assert "values" in normalized["allowed_chats"]
    assert normalized["allowed_chats"]["values"] == []


def test_evaluating_a_message_against_a_legacy_config_does_not_raise():
    subject = block_links.MessageSubject(
        content="look at https://example.com",
        channel_id="100",
        author_role_ids=(),
    )

    evaluation = block_links.evaluate_message(subject, {"block_links": {"enabled": True}})

    assert evaluation is not None
