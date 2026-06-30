"""Shared ASCII branding — the auditr wordmark + the update shimmer animation, used by the
``version`` and ``self update`` commands so the art lives in one place."""

import itertools
import time
from collections.abc import Callable

from rich.console import Group
from rich.live import Live
from rich.text import Text

from auditor.cli.console import ACCENT, err_console

LOGO = (
    " █████╗ ██╗   ██╗██████╗ ██╗████████╗██████╗\n"
    "██╔══██╗██║   ██║██╔══██╗██║╚══██╔══╝██╔══██╗\n"
    "███████║██║   ██║██║  ██║██║   ██║   ██████╔╝\n"
    "██╔══██║██║   ██║██║  ██║██║   ██║   ██╔══██╗\n"
    "██║  ██║╚██████╔╝██████╔╝██║   ██║   ██║  ██║\n"
    "╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝   ╚═╝   ╚═╝  ╚═╝"
)

_LINES = LOGO.split("\n")
_WIDTH = max(len(line) for line in _LINES)
_SHINE = "bold white"
_GLOW = "bold #B7B7FF"


def logo() -> Text:
    """The auditr wordmark as an accent-styled renderable."""
    return Text(LOGO, style=ACCENT)


def _shimmer(col: int) -> Text:
    """The wordmark with a bright shine centred on column ``col`` (the rest dimmed to accent)."""
    text = Text()
    for line_no, line in enumerate(_LINES):
        for ci, ch in enumerate(line):
            if ch == " ":
                text.append(" ")
                continue
            dist = abs(ci - col)
            style = _SHINE if dist == 0 else _GLOW if dist <= 2 else ACCENT
            text.append(ch, style=style)
        if line_no < len(_LINES) - 1:
            text.append("\n")
    return text


def animate(done: Callable[[], bool], label: str = "working…") -> None:
    """Sweep a shine across the wordmark until ``done()`` returns True. No-op when stderr isn't a
    TTY, so a piped / non-interactive run just proceeds silently. The display is transient — it
    clears itself when the work finishes, leaving the final message clean."""
    if not err_console.is_terminal:
        return
    sweep = _WIDTH + 8  # let the shine run off the end before looping back
    with Live(console=err_console, refresh_per_second=24, transient=True) as live:
        for frame in itertools.count():
            if done():
                return
            shine = Group(
                _shimmer(frame % sweep), Text(), Text(f"  {label}", style="dim")
            )
            live.update(shine)
            time.sleep(0.04)
