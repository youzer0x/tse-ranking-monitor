# Scheduled Routine 実行契約

この文書はhash検証済みの日次実行専用契約である。仕様の解釈や変更は行わず、親オーケストレーターが計画・全体検証・公開判定を担う。サブエージェントは指定バッチの調査だけを行う。

## 0. 契約ゲート

最初に `python tools/runtime_contract.py check --contract runbook/runtime_contract.lock.json` を実行する。失敗したら何も生成・push・通知せず停止する。成功時は本書だけを日次手順として読み、長文正本を再読しない。

## 1. セッションゲート

`python scripts/wait_for_data.py` を実行する。

- `SKIP`：生成・push・通知をせず正常終了。
- `TIMEOUT`：生成・push・通知をせず非ゼロ終了し、原因を報告。
- `SESSION=YYYY-MM-DD`：その日付を以後の `<S>` とする。manifestより後の未処理営業日があれば最古日をcatch-upする。過去日は壁時計待機しない。

Stage1の入力整合検証が失敗したら停止する。以後の主要段階は `python .claude/hooks/runtime_telemetry.py stage start|end <name> --session <S>` で囲み、失敗時はendへ `--status failed` を付ける。

## 2. Stage1と調査計画

```text
python scripts/build_day_ranking.py --date <S> --kabutan-news --out .work/<S>/ranking.json
python scripts/build_research_plan.py --ranking .work/<S>/ranking.json --out-dir .work/<S>/research
```

`build_research_plan.py` が非ゼロ終了（dispatch予算超過等）なら調査を開始せず、設計逸脱として報告して停止する。エージェント上限はmanifestの `dispatch_budget` とreserve判定だけを正とする。

`research/manifest.json` の `pending` バッチだけを、`.claude/agents/tse-factor-batch-researcher.md` を使って並列調査する。各バッチの委譲直前に `python scripts/reserve_dispatch.py --research-dir .work/<S>/research --batch <batch_id>` を実行し、exit 0以外なら委譲せず停止して報告する。1タスクには `batch_id` と `batch_path` だけを渡し、ranking row、plan、長文仕様を貼り付けない。各返却JSONをmanifestの `result_path` に保存する。

全結果を次で検証・集約する。

```text
python scripts/compile_research_results.py --research-dir .work/<S>/research --strict
python scripts/merge_factors.py --ranking .work/<S>/ranking.json --factors .work/<S>/research/factors.json
python scripts/validate_ranking_quality.py .work/<S>/ranking.json --evidence .work/<S>/research/evidence.json --format json --repair-targets .work/<S>/research/repair_targets.json
```

親は全コードの一意性・充足、材料窓、出典、クラスタ横断因果を検証する。compile失敗は該当バッチだけをreserve経由で再調査する。validatorのfindingは

```text
python scripts/repair_research_plan.py --research-dir .work/<S>/research --repair-targets .work/<S>/research/repair_targets.json
```

を実行し、`pending` に戻ったバッチだけをreserve経由で再調査してcompile/merge/validatorを再実行する。exit 3（再調査上限または総予算の超過）なら公開せず停止する。完了バッチを再送しない。`ranking.json` を手編集しない。空のfactor、ERROR、未対応WARNが残れば公開しない。

## 3. 市場分析（best-effort）

```text
python scripts/build_market_stats.py --date <S> --out-dir .work/<S>/market
python scripts/build_market_brief.py --ranking .work/<S>/ranking.json --evidence .work/<S>/research/evidence.json --stats .work/<S>/market/market_stats_<S>.json --out .work/<S>/market/market_brief_<S>.json
```

`market_brief_<S>.json` だけを根拠パックとして `.work/<S>/market/narrative_<S>.json` をである調で執筆する。値上がり側moverはbrief内のaccepted evidenceを再利用し、再調査しない。値下がり側だけを `movers_context → 株探の具体記事 → Web一次記事` の順で必要最小限に調べる。個人発信、検索要約、銘柄トップ等のlanding pageは出典にしない。当日15:30以降の材料を日中要因にしない。

```text
python scripts/build_market_json.py --date <S> --csv-dir .work/<S>/market --stats .work/<S>/market/market_stats_<S>.json --defaults scripts/market_fragment_defaults.json --narrative .work/<S>/market/narrative_<S>.json --out docs/data/<S>_market.json
python scripts/validate_market_quality.py docs/data/<S>_market.json --format json --repair-targets .work/<S>/market/repair_targets.json
```

findingがあれば指されたpath/ruleだけ最大2回修復する。出典追加・evidence再利用を優先し、本文削除で通さない。市場分析だけが失敗した場合は理由を記録して次へ進み、ランキング公開は止めない。

## 4. 公開・通知

```text
python scripts/publish.py --in .work/<S>/ranking.json --docs docs --pages-url "$PAGES_URL"
git add docs/index.html docs/data
git commit -m "Update TSE day gainers <S>"
git push origin HEAD:main
python scripts/publish.py --in .work/<S>/ranking.json --docs docs --pages-url "$PAGES_URL" --notify
```

`.work/`、reports、認証情報をcommitしない。push前に通知しない。`--notify` がPages上の当日artifact digestとローカル公開物の一致を確認できない場合、Gmail認証不足またはAPI失敗の場合は未送信のまま非ゼロ終了する。

## 5. 最終報告

`<S>`、該当総数/掲載数、主要要因、即確定/待機/catch-up、待機時間とWARN、市場分析の成功/スキップ理由、調査バッチ数・再試行数、validator残件、push/Pages digest/Gmailの結果を1段落で報告する。利用上限に達した場合は最後に完了したstageとtelemetryの保存先も記す。

## 6. 失敗時の通知

契約ゲート成功後にSKIP以外で停止する場合（TIMEOUT、Stage・検証・公開・通知の失敗）、終了前に次を実行し、送信可否に関わらず当初の非ゼロ終了と失敗報告を維持する。

```text
python scripts/notify_failure.py --stage <停止stage> --reason "<一文>"
```

`<S>` 確定済みなら `--session <S>` を、validator残があれば `--repair-targets .work/<S>/research/repair_targets.json` を付ける。
