#!/usr/bin/env python3
"""PreToolUse guard for Edit / Write / MultiEdit.

Three protections, all sourced from CLAUDE.md rules:
  1. Vendored files (every path listed in any vendor.lock.json under the repo,
     plus the lock files themselves) must not be edited here -- the change flow
     goes through market-scripts-common + sync.py. -> deny
  2. Frozen paths listed in protected_paths.txt (e.g. tests/fixtures/) must not
     be modified. -> deny
  3. Edits under tests/ that remove tests or add skip/xfail markers are flagged
     for user confirmation. -> ask

Design rules: decisions are emitted as PreToolUse stdout JSON; any internal
error fails open (exit 0, no block). stdout is reserved for the decision JSON;
diagnostics go to stderr.
"""
import json
import os
import sys

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
SKIP_MARKERS = ("pytest.mark.skip", "pytest.skip(", "pytest.mark.xfail")


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


def canon(path, base=None):
    """Absolute, forward-slash, lowercased form for comparison.

    Windows paths are case-insensitive and may arrive with either separator,
    so we normalise both before comparing.
    """
    if not os.path.isabs(path):
        path = os.path.join(base or "", path)
    return os.path.abspath(path).replace("\\", "/").lower()


def collect_vendored(root):
    """{canonical_path: display_name} for every vendored file under root.

    Walks the tree (pruning hidden dirs and __pycache__) collecting each
    vendor.lock.json, the files it lists (resolved next to the lock), and the
    lock file itself (hand-editing the lock would defeat drift detection).
    """
    protected = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "__pycache__"]
        if "vendor.lock.json" not in filenames:
            continue
        lockpath = os.path.join(dirpath, "vendor.lock.json")
        protected[canon(lockpath)] = "vendor.lock.json"
        try:
            with open(lockpath, encoding="utf-8") as f:
                lock = json.load(f)
        except Exception as exc:
            sys.stderr.write("edit_guard: cannot read %s: %s\n" % (lockpath, exc))
            continue
        for name in (lock.get("files") or {}):
            protected[canon(os.path.join(dirpath, name))] = name
    return protected


def load_protected_prefixes():
    """Repo-root-relative prefixes from protected_paths.txt (one per line)."""
    prefixes = []
    path = os.path.join(HOOK_DIR, "protected_paths.txt")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    prefixes.append(line.replace("\\", "/").lower().lstrip("/"))
    except FileNotFoundError:
        pass
    except Exception as exc:
        sys.stderr.write("edit_guard: cannot read protected_paths.txt: %s\n" % exc)
    return prefixes


def count_markers(text):
    """(number of test functions, number of skip/xfail markers) in text."""
    tests = text.count("def test_")
    skips = sum(text.count(marker) for marker in SKIP_MARKERS)
    return tests, skips


def test_weakening_reason(rel, abs_path, tool_input):
    """Return an 'ask' reason if this edit removes tests or adds skips, else None."""
    if not (rel.startswith("tests/") and rel.endswith(".py")):
        return None

    edits = tool_input.get("edits")
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if isinstance(edits, list) and edits:
        # MultiEdit: aggregate across all edits.
        old_text = "".join(e.get("old_string", "") for e in edits)
        new_text = "".join(e.get("new_string", "") for e in edits)
    elif old is not None or new is not None:
        old_text, new_text = old or "", new or ""
    elif "content" in tool_input:
        # Write: compare the new content against what is on disk.
        try:
            with open(abs_path, encoding="utf-8") as f:
                old_text = f.read()
        except FileNotFoundError:
            return None  # new test file -- adding tests is encouraged
        except Exception:
            return None
        new_text = tool_input.get("content") or ""
    else:
        return None

    old_tests, old_skips = count_markers(old_text)
    new_tests, new_skips = count_markers(new_text)
    if new_tests < old_tests or new_skips > old_skips:
        return ("tests/ の変更がテスト削除または skip/xfail 追加を含む。CLAUDE.md は"
                "テスト弱体化を黙って行うことを禁じている。仕様変更が理由なら、"
                "ユーザーに説明して同意を得た上で許可すること。")
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # unparsable input -- fail open

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        sys.exit(0)

    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    base = payload.get("cwd") or root
    abs_path = file_path if os.path.isabs(file_path) else os.path.join(base, file_path)
    target = canon(file_path, base)
    root_c = canon(root)

    # 1. Vendored files.
    try:
        vendored = collect_vendored(root)
    except Exception as exc:
        sys.stderr.write("edit_guard: walk failed: %s\n" % exc)
        vendored = {}
    if target in vendored:
        emit("deny",
             "%s は market-scripts-common からベンダリングされた共有スクリプトのため、"
             "本リポでは編集禁止（CI の check_vendor.py が fail する）。正しいフロー："
             "market-scripts-common の src/ を修正 → pytest → VERSION 更新・commit・tag "
             "→ `python sync.py` で全消費リポへ配布 → 本リポでコミット。"
             % vendored[target])

    # Repo-root-relative path for the remaining, path-scoped checks.
    if not target.startswith(root_c + "/"):
        sys.exit(0)  # outside the project -- not our concern
    rel = target[len(root_c) + 1:]

    # 2. Frozen protected prefixes.
    for prefix in load_protected_prefixes():
        if rel.startswith(prefix):
            emit("deny",
                 "%s は変更禁止の保護パス（%s）。tests/fixtures/ は特定日付の凍結"
                 "スナップショットであり更新しない（CLAUDE.md テスト規範）。"
                 % (rel, prefix))

    # 3. Test weakening -> ask.
    reason = test_weakening_reason(rel, abs_path, tool_input)
    if reason:
        emit("ask", reason)

    sys.exit(0)


if __name__ == "__main__":
    main()
