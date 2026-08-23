"""``auditr-observer`` — the observer daemon's lifecycle and hook client.

Deliberately outside the ``auditor`` package and stdlib-only: hooks run on every session event and
``import auditor`` costs ~0.23 s. The daemon lands in a later slice; every subcommand is inert here
and always exits 0 so a hook can never fail a session.
"""

import argparse
import importlib.metadata
import sys

# Wire-compat literal; auditor/observer/__init__.py declares the same value and a test pins them.
OBSERVER_API_VERSION = 1

_UNAVAILABLE = "auditr-observer: not available in this release"
_LIFECYCLE = ("ensure", "start", "stop", "status", "open")


def _version() -> str:
    try:
        return importlib.metadata.version("auditr")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    """Full observer command surface: the lifecycle verbs plus ``hook <event> --client <c>``."""
    parser = argparse.ArgumentParser(
        prog="auditr-observer", description="auditor observer client."
    )
    parser.add_argument(
        "--version", action="version", version=f"auditr-observer {_version()}"
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in _LIFECYCLE:
        subparsers.add_parser(name)
    hook = subparsers.add_parser("hook")
    hook.add_argument("event")
    hook.add_argument("--client", default="claude")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Report that the observer is not in this release, returning 0 so a hook can never fail a session."""
    build_parser().parse_args(argv)
    print(_UNAVAILABLE, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
