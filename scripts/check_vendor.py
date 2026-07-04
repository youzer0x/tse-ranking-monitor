# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""ベンダリング済み共有スクリプトのドリフト検知（CI・ローカル共用）。

同じディレクトリの vendor.lock.json に記録された sha256（改行を LF に正規化して算出）と
実ファイルを突合し、不一致・欠落があれば exit 1。

共有スクリプトを変更したくなったら：
  1. market-scripts-common リポで正本（src/）を修正しテストを通す
  2. VERSION を上げ、commit & tag
  3. python sync.py で全消費リポへ配布 → 各リポでコミット

usage: python scripts/check_vendor.py
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "vendor.lock.json")


def norm_sha256(path):
    """改行を LF に正規化した内容の sha256（Windows/CRLF と CI/LF で一致させる）。"""
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    if not os.path.exists(LOCK):
        print(f"NG: {LOCK} が無い（sync.py 未実行？）")
        return 1
    with open(LOCK, encoding="utf-8") as f:
        lock = json.load(f)
    src = lock.get("source", "market-scripts-common")
    bad = []
    for name, expect in sorted(lock.get("files", {}).items()):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            bad.append((name, "missing"))
            continue
        actual = norm_sha256(p)
        if actual != expect:
            bad.append((name, "modified"))
    if bad:
        print(f"NG: ベンダリング済み共有スクリプトが lock と不一致（{len(bad)} 件）:")
        for name, why in bad:
            print(f"  - {name}: {why}")
        print(f"これらは {src} からのベンダリングです。直接編集せず、")
        print(f"{src} 側の src/ を修正して sync.py で再配布してください。")
        return 1
    print(f"OK: vendored files match {src} v{lock.get('version', '?')} "
          f"({len(lock.get('files', {}))} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
