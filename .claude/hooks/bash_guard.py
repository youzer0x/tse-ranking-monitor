#!/usr/bin/env python3
"""PreToolUse guard for Bash commands.

Which checks run is decided per repo by bash_guard.json ("checks": [...]):

  - tz_jst:       deny commands using TZ='Asia/Tokyo' (it returns UTC in this
                  environment; JST must be fetched with TZ='JST-9'). news repos.
  - commit_gate:  before a `git ... commit`, run check_vendor.py (if configured
                  and present) and `python -m pytest` in the committed repo;
                  block the commit on failure. Optionally 'ask' when src/ is
                  staged without a VERSION bump (market-scripts-common).
  - sync_release: before `python sync.py` (without --check), require a clean
                  worktree and a tag v<VERSION> pointing at HEAD.
                  market-scripts-common only.

Design rules: decisions are emitted as PreToolUse stdout JSON; any internal
error fails open (exit 0, no block). A timed-out hook does NOT block, so the
wiring gives the Bash hook a generous timeout for pytest. stdout is reserved
for the decision JSON; diagnostics go to stderr.
"""
import json
import os
import re
import subprocess
import sys

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))

TZ_RE = re.compile(r"TZ=[\"']?Asia/Tokyo")
COMMIT_RE = re.compile(r"\bgit\b[^;&|]*\bcommit\b")
DASH_C_RE = re.compile(r"\bgit\b\s+(?:-c\s+\S+\s+)*-C\s+(\S+)")
COMMIT_ALL_RE = re.compile(r"\bcommit\b[^;&|]*(?:--all\b|\s-[a-z]*a)")
SYNC_RE = re.compile(r"python[0-9.]*\s+(?:\S*[/\\])?sync\.py\b")


def emit(decision, reason):
    """Print a PreToolUse decision to stdout and exit."""
    # ensure_ascii=True keeps the bytes pure ASCII (\uXXXX), so the JSON is
    # decoded correctly regardless of the Windows console/pipe code page.
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def canon(path):
    return os.path.abspath(path).replace("\\", "/").lower()


def project_root(payload):
    return os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()


def load_config():
    try:
        with open(os.path.join(HOOK_DIR, "bash_guard.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run(cmd, cwd):
    """Run a subprocess; return (returncode_or_None, combined_output)."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout or ""
    except Exception as exc:
        return None, str(exc)


def tail(text, n=40):
    return "\n".join(text.strip().splitlines()[-n:])


def check_tz(command):
    if TZ_RE.search(command):
        emit("deny",
             "この環境では TZ='Asia/Tokyo' は UTC を返す。JST の取得は "
             "TZ='JST-9' date を使うこと（CLAUDE.md 厳守事項）。")


def check_commit(command, payload, cfg):
    if not COMMIT_RE.search(command):
        return
    root = project_root(payload)
    cwd = payload.get("cwd") or root

    # Which repo is being committed? Honour `git -C <dir>`.
    m = DASH_C_RE.search(command)
    if m:
        eff = m.group(1).strip("\"'")
        eff = eff if os.path.isabs(eff) else os.path.join(cwd, eff)
    else:
        eff = cwd
    rc, top = run(["git", "-C", eff, "rev-parse", "--show-toplevel"], eff)
    if rc != 0:
        return  # not a git repo / git error -- fail open
    target_root = top.strip()
    if canon(target_root) != canon(root):
        return  # committing a different repo -- this repo's tests would be wrong

    gate = cfg.get("commit_gate", {})
    code_paths = [p.replace("\\", "/").lower() for p in gate.get("code_paths", [])]

    # Docs-only fast path: only run the gate when code is (or may be) involved.
    #   - `git add` in the same command: staging happens after this hook, so the
    #     staged diff is stale -> must gate.
    #   - `commit -a/--all`: bypasses the index -> must gate.
    #   - otherwise inspect the staged diff for code paths.
    run_gate = ("git add" in command) or bool(COMMIT_ALL_RE.search(command))
    if not run_gate:
        rc_s, staged = run(["git", "-C", target_root, "diff", "--cached",
                            "--name-only"], target_root)
        if rc_s == 0:
            for name in staged.splitlines():
                low = name.strip().replace("\\", "/").lower()
                if low and any(low.startswith(cp) for cp in code_paths):
                    run_gate = True
                    break
    if not run_gate:
        return  # docs-only (or empty) commit -- skip fast, mirrors CI triggers

    # check_vendor.py (drift gate).
    if gate.get("run_check_vendor") and os.path.exists(
            os.path.join(target_root, "scripts", "check_vendor.py")):
        rc_v, out = run(["python", "scripts/check_vendor.py"], target_root)
        if rc_v not in (0, None):
            emit("deny",
                 "ベンダリング済みファイルが lock と不一致のため commit をブロック。"
                 "market-scripts-common 側で修正し sync.py で再配布すること。\n\n"
                 + tail(out))

    # pytest.
    rc_p, out = run(["python", "-m", "pytest"], target_root)
    if rc_p not in (0, None):
        emit("deny",
             "テスト失敗のため commit をブロック（CLAUDE.md: 失敗状態で commit しない）。"
             "まず実装側のバグを疑うこと。\n\n" + tail(out))

    # VERSION-bump reminder (market-scripts-common).
    if gate.get("require_version_bump"):
        rc_n, staged = run(["git", "-C", target_root, "diff", "--cached",
                            "--name-only"], target_root)
        if rc_n == 0:
            names = [s.strip().replace("\\", "/") for s in staged.splitlines()]
            touches_src = any(n.startswith("src/") for n in names)
            touches_version = any(n == "VERSION" for n in names)
            if touches_src and not touches_version:
                emit("ask",
                     "src/ を変更しているが VERSION が未更新。リリースフロー"
                     "（VERSION 更新 → commit & tag vX.Y.Z → sync.py）に従うか、"
                     "意図的な場合のみ許可してください。")


def check_sync(command, payload):
    if not SYNC_RE.search(command) or "--check" in command:
        return
    root = project_root(payload)

    def flow(missing):
        return ("配布前提が未達（%s）。手順：1) src/ を修正し pytest 2) VERSION と "
                "README の CHANGELOG を更新 3) commit & tag v<VERSION> & push 4) "
                "`python sync.py` 5) 各消費リポで diff→pytest→commit→push。"
                "検証のみなら `python sync.py --check`。" % missing)

    rc, out = run(["git", "-C", root, "status", "--porcelain", "--",
                   "src", "VERSION", "manifest.json", "sync.py"], root)
    if rc == 0 and out.strip():
        emit("deny", flow("src/・VERSION 等に未コミットの変更あり。これを配布すると "
                          "実体を含まない HEAD ハッシュが lock に刻印され消費リポの CI が壊れる"))

    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            ver = f.read().strip()
    except Exception:
        return  # no VERSION -- fail open
    tag = "v" + ver
    rc_t, _ = run(["git", "-C", root, "rev-parse", "-q", "--verify",
                   "refs/tags/" + tag], root)
    if rc_t not in (0,):
        emit("deny", flow("タグ %s が存在しない" % tag))
    rc_a, tag_sha = run(["git", "-C", root, "rev-parse", tag + "^{commit}"], root)
    rc_h, head_sha = run(["git", "-C", root, "rev-parse", "HEAD"], root)
    if rc_a == 0 and rc_h == 0 and tag_sha.strip() != head_sha.strip():
        emit("deny", flow("タグ %s が HEAD を指していない" % tag))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command")
    if not command:
        sys.exit(0)

    cfg = load_config()
    checks = cfg.get("checks", [])
    try:
        if "tz_jst" in checks:
            check_tz(command)
        if "sync_release" in checks:
            check_sync(command, payload)
        if "commit_gate" in checks:
            check_commit(command, payload, cfg)
    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write("bash_guard: %s\n" % exc)
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
