"""A one-off migration script (role=script). The ``__name__`` guard marks it as a script,
which gets the relaxed policy."""

import sys


def run(target: str) -> int:
    print(f"migrating {target}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "default"))
