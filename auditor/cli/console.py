"""Shared rich consoles + accent for the CLI — defined once here and imported everywhere, rather
than a ``Console`` instantiated ad hoc in each command module.

``console`` is stdout — human-facing results (the scan summary, the version banner). ``err_console``
is stderr — status, progress, warnings, errors — kept off stdout so the JSON/SARIF an agent parses
is never corrupted. rich auto-disables styling + spinners when the stream isn't a TTY.
"""

from rich.console import Console

ACCENT = "#7C7CFF"

console = Console()  # stdout — human-facing results
err_console = Console(stderr=True)  # stderr — status, progress, errors
