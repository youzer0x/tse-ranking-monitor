"""Installed CLI; the scripts/*.py entry points remain compatible."""

import importlib
import sys

COMMANDS = {
    "pipeline": "pipeline", "ranking": "ranking", "gate": "gate",
    "plan": "research.plan", "compile": "research.evidence",
    "publish": "publishing.publisher", "quality": "quality.ranking",
    "market-quality": "quality.market", "private": "runtime.private_cli",
}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print("Usage: tse-ranking {" + ",".join(COMMANDS) + "} [arguments]")
        return 0 if not args or args[0] in {"-h", "--help"} else 2
    module = importlib.import_module("tse_ranking_monitor." + COMMANDS[args.pop(0)])
    # Older entry points parse sys.argv themselves.
    previous = sys.argv
    try:
        sys.argv = [previous[0], *args]
        return module.main()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
