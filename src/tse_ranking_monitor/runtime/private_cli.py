"""Inspect/export private evidence and resolve uncertain Gmail deliveries."""

import argparse
from pathlib import Path
from .private_store import archive_session, export_private_state, expired_archives, resolve_delivery


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("archive")
    archive.add_argument("--session", required=True)
    export = sub.add_parser("export")
    export.add_argument("--destination", type=Path, required=True)
    retention = sub.add_parser("retention", help="List expired evidence archives; ledger is always retained")
    retention.add_argument("--keep-days", type=int, default=180)
    retention.add_argument("--apply", action="store_true", help="Delete the listed expired archives")
    resolve = sub.add_parser("resolve-delivery")
    resolve.add_argument("--key", required=True)
    resolve.add_argument("--status", choices=("sent", "not-sent"), required=True)
    resolve.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "archive":
            print(archive_session(args.root, args.root / ".work" / args.session))
        elif args.command == "resolve-delivery":
            resolve_delivery(args.root, args.key, args.status, args.reason)
        elif args.command == "retention":
            for path in expired_archives(args.root, keep_days=args.keep_days):
                print(path)
                if args.apply:
                    path.unlink()
        else:
            print(export_private_state(args.root, args.destination))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
