from __future__ import annotations

import sys

from .pilot_operation import main as pilot_main
from .runtime import main as runtime_main


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in {"pilot-run", "pilot-status", "pilot-loop"}:
        raise SystemExit(pilot_main(argv))
    runtime_main()


if __name__ == "__main__":
    main()
