"""Time sources for the controller.

Deadlines are judged by a monotonic clock so system time correction or a time
zone change cannot move them. Raw records are stamped in UTC. Both readings
come from an injectable clock, so tests never depend on wall-clock time.
"""

import time
from datetime import datetime, timezone

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class SystemClock:
    """The production clock: UTC for records, monotonic for elapsed time."""

    def utc_now(self):
        return datetime.now(timezone.utc)

    def monotonic_ms(self):
        return time.monotonic_ns() // 1_000_000


def iso_utc(moment):
    """Render a UTC datetime in the record format used across all artifacts."""
    return moment.astimezone(timezone.utc).strftime(UTC_FORMAT)
