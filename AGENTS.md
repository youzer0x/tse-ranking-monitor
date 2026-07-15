# tse-ranking-monitor（作業規範）

東証の日中値上がり率ランキングを日次生成し、GitHub Pages と Gmail で配信するリポジトリである。

## 必読順

対話的な調査、実装、仕様変更、レビューでは、作業開始前に次を順番どおり全文読む。

1. `vendor/tse-ranking-digest/SKILL.md` — 抽出・時価総額・材料窓・調査方法論
2. `vendor/tse-ranking-digest/reference/sources.md` — 採用可能な出典と禁止ソース
3. `runbook/DAILY_ROUTINE.md` — ゲート、ランキング生成、調査、公開、通知
4. `specs/MARKET_ANALYSIS.md` — 市場分析JSON、文体、因果・出典規律

Scheduled Routineの日次実行だけは、`python tools/runtime_contract.py check --contract runbook/runtime_contract.lock.json` が成功した後、`runbook/RUNTIME_CONTRACT.md` のみを実行契約として読んでよい。checkが失敗した場合は全文読みにフォールバックせず停止し、開発者が契約を再生成する。この例外は日次の静的プロンプト再送を減らすためのもので、対話・開発作業には適用しない。

## 単一の真実源

- ランキング方法論は `vendor/tse-ranking-digest/` の版固定スナップショットを正本とする。
- `scripts/jquants.py`、`business_day.py`、`kabutan_pts.py`、`tdnet.py`、`market_cap_*.py`、`merge_factors.py` と `.claude/agents/stock-factor-researcher.md` は `market-scripts-common` が正本である。
- 本リポ固有の実装は `src/tse_ranking_monitor/`、互換CLIは `scripts/`、公開物は `docs/`、一時物は `.work/<SESSION>/` に置く。
- 日次手順とランキング品質はrunbook、市場分析固有の表示・ナラティブ規則はspecにのみ追記し、ここへ複製しない。

## 絶対停止条件

- ゲートが `SKIP` なら生成・push・通知を行わない。
- runtime contractの欠落・hash不一致なら日次実行を開始しない。
- ゲートが `TIMEOUT`、Stage1の入力整合検証失敗、Stage2 evidenceのstrict compile失敗、ランキングに空の `factor`、またはランキング品質validatorの未対応ERROR/WARNが残る場合はランキングを公開しない。
- 市場分析はbest-effortであり、その生成失敗だけでランキング公開を止めない。失敗理由は最終報告する。
- 公開ファイルをpushする前にメールを送らない。Pages上の当日artifact digestがローカル公開物と一致しない限り通知しない。
- Pages確認のタイムアウト、Gmail認証不足、Gmail API失敗時は未送信のまま非ゼロ終了し、失敗を報告する。

## 編集禁止・公開境界

- vendor管理ファイルを消費リポジトリで直接編集しない。変更は各正本で行い、lock付き同期手順で取り込む。
- `ranking.json` を手編集しない。要因修正は `.work/<SESSION>/factors.json` を更新し、`scripts/merge_factors.py` で反映する。
- `.work/`、`reports/`、認証情報をcommitしない。公開ルート `docs/` には生成済み成果物だけを置く。
- 過去の `docs/data/` を構造整理のために書き換えない。
