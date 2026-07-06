# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""サブエージェントの調査結果を ranking.json の rows へ機械マージする。

Stage2（変動要因の裏取り）で stock-factor-researcher サブエージェントが返した結果
（JSON 配列）を docs/tmp/factors.json に集め、本スクリプトが ranking.json の各 row の
factor / factor_kind **のみ** を書き換える。rows の順序・他フィールドには一切触れない
（LLM が 50 行の JSON を再シリアライズする際のフィールド欠落・順序崩れ・name 上書きを
構造的に防ぐ）。

- 検証：code が rows に存在／factor が非空文字列／factor_kind ∈ {開示, 報道, テーマ}
- 出力：MERGED n/total ／ REJECTED <code>: 理由 ／ MISSING <codes>（factor が空のままの row）
- MISSING / REJECTED の行は呼び出し側（親エージェント）がインライン調査で埋め、
  factors.json を更新して本スクリプトを再実行する（何度実行してもよい。同一 code は後勝ち）。
- exit code：構造エラー（JSON 不正・rows 欠落・factors が配列でない等）のみ 1。
  REJECTED / MISSING があっても 0（親のフォールバックで埋める前提）。

usage:
  python scripts/merge_factors.py --ranking docs/tmp/ranking.json --factors docs/tmp/factors.json
"""
import argparse
import json
import os
import sys

VALID_KINDS = ("開示", "報道", "テーマ")


def merge(ranking, factors):
    """factors のエントリを ranking["rows"] へ適用する（ranking は in-place 更新）。

    返り値: (merged_codes, rejected, missing)
      merged_codes: 適用した code のリスト（適用順・重複は後勝ちで一度だけ数える）
      rejected:     [(label, 理由), ...]
      missing:      マージ後も factor が空のままの row の code リスト
    """
    rows = ranking.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ranking.json に rows（配列）が無い")
    if not isinstance(factors, list):
        raise ValueError("factors.json はエントリの JSON 配列であること")

    by_code = {}
    for row in rows:
        code = str(row.get("code", ""))
        if code:
            by_code[code] = row

    merged_codes = []
    rejected = []
    for i, entry in enumerate(factors):
        label = f"entry[{i}]"
        if not isinstance(entry, dict):
            rejected.append((label, "オブジェクトでない"))
            continue
        code = str(entry.get("code", "")).strip()
        if code:
            label = code
        row = by_code.get(code)
        if row is None:
            rejected.append((label, "rows に存在しない code"))
            continue
        factor = entry.get("factor")
        if not isinstance(factor, str) or not factor.strip():
            rejected.append((label, "factor が空"))
            continue
        kind = entry.get("factor_kind")
        if kind not in VALID_KINDS:
            rejected.append((label, f"factor_kind が不正: {kind!r}"))
            continue
        if code in merged_codes:
            merged_codes.remove(code)  # 後勝ち（最新の位置で数え直す）
        row["factor"] = factor.strip()
        row["factor_kind"] = kind
        merged_codes.append(code)

    missing = [str(r.get("code", "")) for r in rows
               if not (isinstance(r.get("factor"), str) and r["factor"].strip())]
    return merged_codes, rejected, missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="サブエージェント調査結果を ranking.json へマージする")
    ap.add_argument("--ranking", required=True, help="build スクリプトが出力した ranking.json")
    ap.add_argument("--factors", required=True, help="調査結果エントリの JSON 配列")
    args = ap.parse_args(argv)

    try:
        with open(args.ranking, encoding="utf-8") as f:
            ranking = json.load(f)
        with open(args.factors, encoding="utf-8") as f:
            factors = json.load(f)
        merged, rejected, missing = merge(ranking, factors)
    except (OSError, ValueError) as e:
        print(f"NG: {e}")
        return 1

    tmp = args.ranking + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ranking, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, args.ranking)

    total = len(ranking.get("rows", []))
    print(f"MERGED {len(merged)}/{total}")
    for label, why in rejected:
        print(f"REJECTED {label}: {why}")
    if missing:
        print(f"MISSING {','.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
