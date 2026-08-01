"""Deterministic test doubles for the injectable clock and token seams."""

from datetime import datetime, timedelta, timezone


# Deliberately not "around now", so a test asserting these stamps proves the
# injected clock was used instead of the wall clock.
FIXED_START_UTC = datetime(2026, 3, 14, 1, 59, 26, tzinfo=timezone.utc)


class FixedClock:
    """A clock that moves only by test control or a fixed tick per reading."""

    def __init__(self, start_utc=None, monotonic_start_ms=0, auto_advance_ms=0):
        self._start_utc = start_utc or FIXED_START_UTC
        self._monotonic_ms = monotonic_start_ms
        self._offset_ms = 0
        self._auto_advance_ms = auto_advance_ms

    def utc_now(self):
        return self._start_utc + timedelta(milliseconds=self._offset_ms)

    def monotonic_ms(self):
        current = self._monotonic_ms + self._offset_ms
        self._offset_ms += self._auto_advance_ms
        return current

    def advance_ms(self, milliseconds):
        self._offset_ms += milliseconds


class ScriptedTokenSource:
    """Yields pre-agreed run-id tokens instead of random ones."""

    def __init__(self, tokens):
        self._tokens = list(tokens)

    def __call__(self):
        if not self._tokens:
            raise AssertionError("ScriptedTokenSource exhausted")
        return self._tokens.pop(0)
