"""Import methodology bytes from an explicit, committed source revision."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

FILES = ("SKILL.md", "reference/sources.md", "templates/tse_ranking.md")
SOURCE = "skills/tse-ranking-digest/"


def sync(source_repo, destination, revision):
    def git(*args):
        return subprocess.run(["git", "-C", str(source_repo), *args], check=True, capture_output=True).stdout
    commit = git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
    payloads = {name: git("show", f"{commit}:{SOURCE}{name}").replace(b"\r\n", b"\n") for name in FILES}
    target = Path(destination) / "vendor/tse-ranking-digest"
    for name, payload in payloads.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    lock = {"source": "news-financial-market/skills/tse-ranking-digest", "commit": commit,
            "synced_at": datetime.now(timezone.utc).date().isoformat(),
            "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}}
    (target / "vendor.lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(sync(args.source, Path(__file__).resolve().parents[1], args.revision))


if __name__ == "__main__":
    main()
