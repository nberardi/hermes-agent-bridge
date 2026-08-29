from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        from .check import main as check_main

        return check_main(sys.argv[2:])
    if len(sys.argv) > 1:
        print(
            "usage: python -m hermes_agent_bridge [check [--env-file PATH]]",
            file=sys.stderr,
        )
        return 2
    from .server import main as server_main

    server_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
