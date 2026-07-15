# CLAUDE.md — 対話開発の規範

日次の無人運用は `AGENTS.md` が定めるhash検証済み `runbook/RUNTIME_CONTRACT.md` に従う。本ファイルは対話的なコード変更と検証の規範を定める。

## 構造

- `src/tse_ranking_monitor/`：本リポ固有の実装本体
- `scripts/`：既存CLI互換ラッパーと `market-scripts-common` 由来の共有ベンダー
- `vendor/tse-ranking-digest/`：ランキング方法論の版固定スナップショット
- `tools/`：vendor整合性などの保守チェック
- `runbook/`：日次手順、セットアップ、貼り付けプロンプト
- `specs/`：市場分析固有のデータ・表示・執筆仕様
- `docs/`：GitHub Pagesの公開成果物のみ
- `.work/`：日次一時物。追跡・公開しない

## テスト規範

- `src/`、`scripts/`、`tools/` のコードを変更したら、commit前に `python -m pytest` を実行する。
- テストが1件でも失敗した状態でcommitしない。まず実装側の不具合を疑い、テスト削除、skip追加、assert弱体化で通さない。
- 仕様変更で期待値を変える場合は理由を説明し、対になるテストを更新する。新しい関数や分岐にはテストを追加する。
- テストはネットワーク、認証情報、実行日に依存させない。外部APIをmonkeypatchし、固定日を渡す。
- `tests/fixtures/` は凍結スナップショットである。`docs/data/` の実データをテストから直接読まない。

## SOTとベンダリング

- `scripts/jquants.py`、`business_day.py`、`kabutan_pts.py`、`tdnet.py`、`market_cap_jquants.py`、`market_cap_yahoo.py`、`merge_factors.py` と `.claude/agents/stock-factor-researcher.md` は `market-scripts-common` が正本であり、このリポジトリでは直接編集しない。
- ランキング方法論は `vendor/tse-ranking-digest/` が正本であり、同梱lockにない編集をしない。
- 本体変更後は `python scripts/check_vendor.py`、`python tools/check_methodology_vendor.py`、`python -m pytest` を順に実行する。
- vendor更新は正本側で変更・テスト・version更新後、同期ツールでlockとともに取り込む。同期直後も全テストを実行する。

## 公開境界

- `.work/`、`reports/`、認証情報をcommitしない。
- `ranking.json` を手編集せず、`factors.json` と `scripts/merge_factors.py` を使う。
- 構造整理を理由に過去の `docs/data/` を書き換えない。

## 検証コマンド

```bash
python -m pip install -r requirements-dev.txt
python scripts/check_vendor.py
python tools/check_methodology_vendor.py
python -m pytest
```

CIは `.github/workflows/tests.yml` がコード、テスト、vendor、方法論、runbook/specの変更を検知して同じ検証を実行する。日次の `docs/data/` 更新だけでは起動しない。
