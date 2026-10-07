"""Deterministic daily stages. AI produces research results and narrative only."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from .contracts import validate_ranking_document
from .publishing import publisher
from .publishing.validation import require_compiled_evidence
from .research.plan import _atomic_write_json
from .runtime.contract import verify_contract_lock
from .runtime.private_store import archive_session
from .runtime.telemetry import TelemetryWriter
from .runtime import status as run_status

PHASES = ("start", "research", "publish", "deploy", "notify")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(root):
    paths = []
    for directory in ("src", "scripts", "runbook", "specs", "vendor"):
        paths.extend(path for path in (root / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix in {".py", ".json", ".md", ".js", ".css", ".html"})
    paths.extend(root / name for name in ("requirements.lock", "pyproject.toml"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


@contextmanager
def session_lock(root, session):
    """The OS releases this SQLite write lock even when a worker crashes."""
    path = root / ".work" / session / "pipeline.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=0)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield
    finally:
        db.close()


class Pipeline:
    def __init__(self, root, session):
        self.root = Path(root).resolve()
        if date.fromisoformat(session).isoformat() != session:
            raise ValueError("session must be YYYY-MM-DD")
        self.session = session
        self.work = self.root / ".work" / session
        self.research_dir = self.work / "research"
        self.market = self.work / "market"
        self.checkpoint = self.work / "checkpoint.json"
        failures = verify_contract_lock(self.root)
        if failures:
            raise ValueError("runtime contract failed: " + "; ".join(failures))
        self.code_digest = fingerprint(self.root)
        self.gate_confirmed = False
        self.state = read_json(self.checkpoint) if self.checkpoint.exists() else {
            "schema_version": 1, "session": session, "code_digest": self.code_digest, "completed": {}}
        if self.state.get("code_digest") != self.code_digest:
            raise ValueError("code/config changed since this run; preserve its archive and start in a clean workspace")

    def run(self, arguments, *, accepted=(0,)):
        process = subprocess.run(arguments, cwd=self.root, capture_output=True,
                                 encoding="utf-8", errors="replace",
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if process.stderr:
            print(process.stderr, file=sys.stderr, end="")
        if process.returncode not in accepted:
            raise RuntimeError(f"{Path(arguments[0]).name} {arguments[1]} failed (exit {process.returncode})")
        return process

    def script(self, name, *arguments, accepted=(0,)):
        return self.run([sys.executable, str(self.root / "scripts" / name),
                         *map(str, arguments)], accepted=accepted)

    def hashes(self, paths):
        return {str(Path(path).relative_to(self.root)): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                for path in paths}

    def completed(self, phase):
        record = self.state["completed"].get(phase)
        if not record:
            return False
        for name, digest in record["files"].items():
            path = self.root / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"{phase} checkpoint changed: {name}; rerun that phase before continuing")
        return True

    def save(self, phase, paths=(), **report):
        self.state["completed"][phase] = {"files": self.hashes(paths), **report}
        self.state.pop("failure", None)
        _atomic_write_json(self.checkpoint, self.state)
        archive_session(self.root, self.work, report={"phase": phase, **report})

    def execute(self, phase, pages_url):
        index = PHASES.index(phase)
        if index and not self.completed(PHASES[index - 1]):
            raise ValueError(f"complete {PHASES[index - 1]} before {phase}")
        # A repeated stage rebuilds all dependent artifacts before they can deploy.
        if phase in {"start", "deploy", "notify"} and self.completed(phase):
            print(f"{phase}: already complete")
            return
        for downstream in PHASES[index:]:
            self.state["completed"].pop(downstream, None)
        _atomic_write_json(self.checkpoint, self.state)
        self.mark_stage(phase, "start")
        try:
            getattr(self, phase)(pages_url)
            self.mark_stage(phase, "end", "ok")
        except Exception as exc:
            self.mark_stage(phase, "end", "failed")
            self.state["completed"].pop(phase, None)
            self.state["failure"] = {"phase": phase, "type": type(exc).__name__}
            _atomic_write_json(self.checkpoint, self.state)
            archive_session(self.root, self.work, report=self.state["failure"])
            raise

    def mark_stage(self, phase, boundary, status=None):
        try:
            TelemetryWriter(self.root).record_stage(self.session, phase, boundary, status)
            run_status.publish_status_quietly(self.root, run_status.collect_status(self.root, self.session))
        except OSError as exc:
            print(f"WARN telemetry: {type(exc).__name__}", file=sys.stderr)

    def start(self, _):
        gate = "SESSION=" + self.session if self.gate_confirmed else self.script("wait_for_data.py", "--date", self.session).stdout.strip()
        if gate == "SKIP":
            print("SKIP")
            return
        if gate != "SESSION=" + self.session:
            raise ValueError("gate did not confirm the requested session")
        print("SESSION=" + self.session)
        stage1 = self.work / "stage1.json"
        self.script("build_day_ranking.py", "--date", self.session, "--kabutan-news", "--out", stage1)
        data = read_json(stage1)
        validate_ranking_document(data, require_stage1_counts=True, require_numeric_fields=True)
        self.script("build_research_plan.py", "--ranking", stage1, "--out-dir", self.research_dir)
        self.save("start", [stage1])
        print(f"Research inputs: {self.research_dir / 'manifest.json'}")

    def research(self, _):
        self.script("compile_research_results.py", "--research-dir", self.research_dir, "--strict")
        ranking = self.work / "ranking.json"
        publisher._atomic_write_bytes(ranking, (self.work / "stage1.json").read_bytes())
        self.script("merge_factors.py", "--ranking", ranking, "--factors", self.research_dir / "factors.json")
        quality = self.script("validate_ranking_quality.py", ranking, "--evidence", self.research_dir / "evidence.json",
                              "--strict", "--format", "json", "--repair-targets", self.research_dir / "repair_targets.json",
                              accepted=(0, 1))
        _atomic_write_json(self.work / "quality-ranking.json", json.loads(quality.stdout))
        if quality.returncode:
            raise ValueError("ranking quality requires repair; see quality-ranking.json")
        require_compiled_evidence(read_json(ranking), self.research_dir)
        market_failure = None
        try:
            self.script("build_market_stats.py", "--date", self.session, "--out-dir", self.market)
            self.script("build_market_brief.py", "--ranking", ranking, "--evidence", self.research_dir / "evidence.json",
                        "--stats", self.market / f"market_stats_{self.session}.json",
                        "--out", self.market / f"market_brief_{self.session}.json")
        except (RuntimeError, ValueError) as exc:
            market_failure = str(exc)
        self.save("research", [ranking], market_failure=market_failure)
        print("Write market narrative from the brief, then run publish. Market generation is best-effort.")

    def publish(self, _):
        draft = self.market / f"{self.session}_market.json"
        narrative = self.market / f"narrative_{self.session}.json"
        failure = self.state["completed"]["research"].get("market_failure")
        if not failure and narrative.exists():
            try:
                self.script("build_market_json.py", "--date", self.session, "--csv-dir", self.market,
                            "--stats", self.market / f"market_stats_{self.session}.json",
                            "--defaults", self.root / "scripts/market_fragment_defaults.json",
                            "--narrative", narrative, "--out", draft)
            except RuntimeError as exc:
                failure = str(exc)
        else:
            failure = failure or "market narrative is unavailable"
        # Passing a missing private path explicitly excludes any prior public draft.
        report = publisher.build(read_json(self.work / "ranking.json"), self.root / "docs",
                                 research_dir=self.research_dir,
                                 market_path=draft if not failure else self.market / "unavailable.json")
        _atomic_write_json(self.work / "quality-market.json", {
            "session": self.session, "passed": not (failure or report["market_failure"]),
            "failure": failure or report["market_failure"]})
        paths = [self.root / "docs/index.html", self.root / "docs/data/manifest.json",
                 self.root / f"docs/data/{self.session}.json"]
        if (self.root / f"docs/data/{self.session}_market.json").exists():
            paths.append(self.root / f"docs/data/{self.session}_market.json")
        self.save("publish", paths, market_failure=failure or report["market_failure"])
        if failure or report["market_failure"]:
            print("WARN market skipped: " + (failure or report["market_failure"]))

    def deploy(self, _):
        require_compiled_evidence(read_json(self.work / "ranking.json"), self.research_dir)
        # A crash after commit/push is recoverable: only create a commit if docs changed.
        changes = self.run(["git", "diff", "--name-only", "HEAD"]).stdout.splitlines()
        if any(not (name == "docs/index.html" or name.startswith("docs/data/")) for name in changes):
            raise ValueError("deployment workspace contains non-public changes")
        self.run(["git", "add", "--", "docs/index.html", "docs/data"])
        changed = self.run(["git", "diff", "--cached", "--quiet"], accepted=(0, 1)).returncode
        if changed:
            self.run(["git", "commit", "-m", f"Update TSE day gainers {self.session}"])
        self.run(["git", "fetch", "origin", "main"])
        from .publishing.promotion import verify_candidate
        verify_candidate(self.root, "claude/pipeline", head="HEAD", base="origin/main")
        pushed = self.run(["git", "push", "origin", "HEAD:main"], accepted=(0, 1, 128))
        if pushed.returncode:
            branch = self.run(["git", "branch", "--show-current"]).stdout.strip()
            if not branch.startswith("claude/"):
                raise ValueError("main push failed and fallback requires a claude/* branch")
            self.run(["git", "push", "origin", "HEAD"])
        revision = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.save("deploy", revision=revision)

    def notify(self, pages_url):
        revision = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
        if revision != self.state["completed"]["deploy"]["revision"]:
            raise ValueError("HEAD changed after deployment")
        self.completed("publish")
        publisher.notify(self.work / "ranking.json", self.root / "docs", pages_url)
        self.save("notify", delivered=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=(*PHASES, "resume"))
    parser.add_argument("--session", help="Required except for start (automatic session gate)")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pages-url", default=os.environ.get("PAGES_URL", "./"))
    args = parser.parse_args(argv)
    try:
        confirmed = False
        if not args.session:
            if args.phase != "start":
                raise ValueError("--session is required for resume and subsequent phases")
            failures = verify_contract_lock(args.root)
            if failures:
                raise ValueError("runtime contract failed: " + "; ".join(failures))
            gate = subprocess.run([sys.executable, str(args.root.resolve() / "scripts/wait_for_data.py")],
                                  cwd=args.root, capture_output=True, encoding="utf-8",
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            print(gate.stderr, file=sys.stderr, end="")
            result = gate.stdout.strip()
            if gate.returncode == 0 and result == "SKIP":
                print("SKIP")
                return 0
            if gate.returncode or not result.startswith("SESSION="):
                raise ValueError("gate failed: " + result)
            args.session = result.removeprefix("SESSION=")
            confirmed = True
        if date.fromisoformat(args.session).isoformat() != args.session:
            raise ValueError("session must be YYYY-MM-DD")
        with session_lock(args.root.resolve(), args.session):
            pipeline = Pipeline(args.root, args.session)
            pipeline.gate_confirmed = confirmed
            phase = args.phase
            if phase == "resume":
                phase = next((item for item in PHASES if not pipeline.completed(item)), None)
                if phase is None:
                    print("All stages are complete")
                    return 0
            pipeline.execute(phase, args.pages_url)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
