# CLAUDE.md — 開発時の規範（Claude Code 向け）

このファイルは **Claude Code（対話的な開発）** が読む。日次の無人ルーチンが従う実行手順は
`AGENTS.md` にある（役割が違うので混ぜない）。

## テスト規範（pytest）

- `scripts/` 配下の `.py` を変更したら、commit の前に必ず `python -m pytest` を実行する。
- テストが1件でも失敗している状態で commit しない。
- テストが失敗したら、**まず実装側のバグを疑う**。期待値を変える必要がある場合は「仕様が
  変わったため」であることをユーザーに説明し、同意を得てからテストを更新する。
  **テストの削除・skip 追加・assert の弱体化を黙って行うことを禁止する**（テストを通すために
  テスト側を書き換えるのは、番犬の口を塞ぐのと同じ）。
- 新しい関数・条件分岐を追加したら、対になるテストを `tests/` に追加する（純粋関数は必須。
  I/O を伴う関数は `tmp_path` フィクスチャで可能な範囲）。
- テストはネットワーク・認証情報・実行日時（`date.today()`）に依存させない。外部 API は
  monkeypatch し、日付は固定値を渡す（`pytest-socket` が通信を機械的に遮断する）。
- `tests/fixtures/` は特定日付の凍結スナップショットであり、更新しない。`docs/data/` の実データを
  テストから直接読まない（publish.py が約30日で削除するため）。

## SOT（単一の真実源）との同期

`business_day.py` / `jquants.py` / `build_market_stats.py` / `build_market_json.py` /
`html_generator.py` 等は、`news-financial-market/skills/tse-ranking-digest/scripts/` を
真実源として `cp` で取り込んだ共有スクリプト。

- 共有スクリプトを本リポジトリで変更したら、真実源側と diff を取り、**双方向に同期**する。
- 真実源側を更新して `cp` で取り込んだ直後も、必ず `python -m pytest` を実行して回帰が
  無いことを確認する（テストは cp のドリフト検知も兼ねる）。

## テストの実行

```bash
python -m pip install -r requirements-dev.txt   # 初回のみ
python -m pytest                                 # 全テスト（数秒・オフラインで完結）
```

CI: `.github/workflows/tests.yml` が push 時（`scripts/`・`tests/`・`requirements*` 変更時）に
自動で `python -m pytest` を回す。docs/data/ への日次コミットでは走らない。
