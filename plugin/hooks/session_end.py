#!/usr/bin/env python3
"""SessionEnd hook: tell the observer daemon this session is gone (spec 13.1)."""

from _common import observe, read_event

OBSERVE_TIMEOUT = 1.0


def main() -> None:
    event = read_event()
    if event is None:
        return
    observe("session-end", event, OBSERVE_TIMEOUT)


if __name__ == "__main__":
    main()
