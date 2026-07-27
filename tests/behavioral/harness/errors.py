"""Errors raised by the behavioral harness.

Every error carries the scenario transcript so a failure is diagnosable
without adding prints.
"""


class HarnessError(AssertionError):
    """Base class; message already includes the transcript when available."""

    def __init__(self, message: str, transcript: str = ""):
        if transcript:
            message = f"{message}\n\n--- Transcript ---\n{transcript}"
        super().__init__(message)


class HarnessProtocolError(HarnessError):
    """The code under test used the interaction API out of order.

    Examples: two initial responses on one interaction, a followup before
    any initial response, editing a deleted message. These mirror real
    Discord API constraints, so a protocol error usually means a real bug.
    """


class LocatorError(HarnessError):
    """A click/select/modal target could not be resolved on screen."""


class ScenarioAssertionError(HarnessError):
    """An expect_* assertion failed."""
