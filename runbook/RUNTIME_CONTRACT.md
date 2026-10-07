# Scheduled Routine 実行契約

この文書はhash検証済みの日次実行専用契約である。仕様の解釈や変更は行わず、親オーケストレーターが計画・全体検証・公開判定を担う。サブエージェントは指定バッチの調査だけを行う。

## 0. 契約ゲート

最初に `python tools/runtime_contract.py check --contract runbook/runtime_contract.lock.json` を実行する。失敗したら何も生成・push・通知せず停止する。成功時は本書だけを日次手順として読み、長文正本を再読しない。

## 1. セッションゲート

契約成功後、checkoutしたリポジトリで Python 3.12 の仮想環境を作って有効化し、lockから導入する。既定の `python` が3.11の環境では仮想環境なしの導入が `requires-python>=3.12` で失敗するため、必ずこの手順で入れる。既に同じlock・コード版を導入済みの `.venv` があれば有効化だけでよい。以後の `python` コマンドはすべてこの仮想環境で実行し、システムの `python` や `/usr/local/bin` のリンクを付け替えない。

```text
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.lock && python -m pip install --no-deps .
```

続いて `python -m tse_ranking_monitor pipeline start` を実行する（ゲート・Stage1・調査計画まで自動実行）。

- `SKIP`：生成・push・通知をせず正常終了。
- `TIMEOUT`：生成・push・通知をせず非ゼロ終了し、原因を報告。
- `SESSION=YYYY-MM-DD`：その日付を以後の `<S>` とする。対象はゲートが選ぶ直近の完了セッションのみ（catch-up窓＝1営業日・再開下限あり）。それより古い未公開営業日はゲートが切り捨てて `WARN 切り捨て=` を出すので最終報告に含め、手動で遡らない。過去日は壁時計待機しない。

Stage1の入力整合検証・時価総額取得が失敗したら停止する。pipelineは前段の成果物とコード版を照合し、stageの開始・終了・失敗を記録する。途中からは `pipeline resume --session <S>` で最初の未完了段階を一つ実行できる。調査JSONとナラティブはAIが作成する。

日次実行では追跡対象のコード・設定（`src/`、`scripts/`、`tools/`、`tests/`、`runbook/`、`.claude/`、`pyproject.toml`、`requirements*` 等）を編集・commit・pushしない。pipeline CLI や scripts がコード起因の例外（Traceback）や想定外の `ERROR` で停止した場合は、修復や回避を試みず §6 の失敗通知を実行して非ゼロ終了する。checkpoint（`.work/<S>/checkpoint.json`・`pipeline.sqlite3`）の手編集・削除、git plumbing による `main` への直接push、インタプリタのリンク付け替えも回避策として行わない。修正は開発者が対話セッションで行い、必要なら手動で再発火する（2026-10-07：実行中のコード修正commitが日次公開commitに混入し、deployの単一commit検証とcheckpointの整合が崩れてGmail未送信のまま終了した）。

## 2. Stage1と調査計画

`pipeline start` が `.work/<S>/stage1.json` と `research/manifest.json` を生成する。Stage1の数値は不変の入力として保存し、要因は後段で `ranking.json` へmergeする。

`build_research_plan.py` が非ゼロ終了（dispatch予算超過等）なら調査を開始せず、設計逸脱として報告して停止する。エージェント上限はmanifestの `dispatch_budget` とreserve判定だけを正とする。

`research/manifest.json` の `pending` バッチだけを、`.claude/agents/tse-factor-batch-researcher.md` を使って並列調査する。各バッチの委譲直前に `python scripts/reserve_dispatch.py --research-dir .work/<S>/research --batch <batch_id>` を実行し、exit 0以外なら委譲せず停止して報告する。1タスクには `batch_id` と `batch_path` だけを渡し、ranking row、plan、長文仕様を貼り付けない。各返却JSONをmanifestの `result_path` に保存する。

全結果を次でstrict compile・merge・品質検査し、市場stats / briefまで生成する。公表時刻のタイムゾーンと実際の材料窓、`check_reasons` と省略条件も検査する。

```text
python -m tse_ranking_monitor pipeline research --session <S>
```

親は全コードの一意性・充足、材料窓、出典、クラスタ横断因果を検証する。compile失敗は該当バッチだけをreserve経由で再調査する。validatorのfindingは

```text
python scripts/repair_research_plan.py --research-dir .work/<S>/research --repair-targets .work/<S>/research/repair_targets.json
```

を実行し、`pending` に戻ったバッチだけをreserve経由で再調査してcompile/merge/validatorを再実行する。exit 3（再調査上限または総予算の超過）なら公開せず停止する。完了バッチを再送しない。`ranking.json` を手編集しない。空のfactor、ERROR、未対応WARNが残れば公開しない。

factorは表示250字以内（リンクはラベルだけ数える）で要因の本質に絞り、書き出しの銘柄名主語、休場日などの自明な背景、業種コード・クラスタID・入力フィールド名、「材料窓」「窓内」「窓外」、「適時開示なし」等の不在の記述を書かない。親が横断検証でfactorを直すときも同じ規律に従う。validatorの字数・書き方の指摘（`RANK_FACTOR_TOO_LONG`・`_JARGON`・`_ABSENCE`・`_INTERNAL_CODE`・`_SELF_NAME_OPENER`）は出典の追加ではなく凝縮で直す。海外企業名は日経等の定着表記で書く（例：米マイクロン、SKハイニックス）。英語の資料だけを読んでも社名を自前で音訳せず、定着表記が不明なら英語の正式名のまま書く（`RANK_FACTOR_NOTATION`／市場分析は `MKT_NAME_NOTATION` で検出し、示された表記へ置き換えて直す）。

## 3. 市場分析（best-effort）

`pipeline research` が市場statsと `market_brief.v2` を生成済み。市場分析だけの失敗はcheckpointに記録され、ランキング公開を止めない。

`market_brief_<S>.json` だけを根拠パックとして `.work/<S>/market/narrative_<S>.json` をである調で執筆する。`accepted_evidence[]` は `code` ごとの `market_note`・claim・参照先sourceの対応であり、claimの `source_ids` が指す同一要素内のsourceだけを根拠に使う（コードをまたいで出典を推測しない）。個別銘柄movers（値上がり/値下がり）は調査・執筆・出力しない（2026-07-16廃止）。個人発信、検索要約、銘柄トップ等のlanding pageは出典にしない。当日15:30以降の材料を日中要因にしない。

```text
python scripts/build_market_json.py --date <S> --csv-dir .work/<S>/market --stats .work/<S>/market/market_stats_<S>.json --defaults scripts/market_fragment_defaults.json --narrative .work/<S>/market/narrative_<S>.json --out .work/<S>/market/<S>_market.json
python scripts/validate_market_quality.py .work/<S>/market/<S>_market.json --strict --format json --repair-targets .work/<S>/market/repair_targets.json
```

findingがあれば指されたpath/ruleだけ最大2回修復する。出典追加・evidence再利用を優先し、本文削除で通さない。市場分析だけが失敗した場合は理由を記録して次へ進み、ランキング公開は止めない。

## 4. 公開・通知

```text
python -m tse_ranking_monitor pipeline publish --session <S>
python -m tse_ranking_monitor pipeline deploy --session <S>
python -m tse_ranking_monitor pipeline notify --session <S> --pages-url "$PAGES_URL"
```

deployは公開物をcommitして `git push origin HEAD:main` を試し、拒否された場合は `git push origin HEAD` で現在の `claude/*` branchへ同じcommitをpushする。読み取り専用の `.github/workflows/validate-routine-publication.yml` が候補を検証し、main上の信頼済み `.github/workflows/promote-routine-publication.yml` が同じ候補を再検証する。候補をcheckoutせず、信頼済みworkflow SHAのコードでGit objectを読む。現行mainの直系・単一commit、公開パス限定、品質合格、信頼済みHTMLとの一致、manifest digest一致の場合だけmainへfast-forwardしてPages buildを要求する。`--notify` はローカルHEADがorigin/mainへ到達するまで最大5分、その後Pages上の当日artifact digest一致まで最大5分待つ。

buildは元のbatch結果をstrict compileし直し、Stage1のdigestとfactorの完全一致、ERROR/WARNなしを必須にする。市場分析は作業場所で検査し、合格分だけdocsへコピーする。市場分析失敗は最終報告へ残す。

通知は日付・公開内容・宛先集合で冪等化する。`pending` で止まった送信は、Gmailで履歴を確認して `python -m tse_ranking_monitor private resolve-delivery --key <key> --status sent|not-sent --reason "確認内容"` を実行するまで再送しない。送信済みは自動でスキップする。

`.work/`、reports、認証情報をcommitしない。push前に通知しない。直接pushとfallbackの両方が未達、Pages digest不一致、Gmail認証不足またはAPI失敗の場合は未送信のまま非ゼロ終了する。直接push拒否後にfallbackが成功した場合は正常配信として扱う。

## 5. 最終報告

`<S>`、該当総数/掲載数、主要要因、即確定/待機/catch-up、切り捨て日、待機時間とWARN、市場分析の成功/スキップ理由、調査バッチ数・再試行数、validator残件、push/Pages digest/Gmailの結果を1段落で報告する。利用上限に達した場合は最後に完了したstageとtelemetryの保存先も記す。

## 6. 失敗時の通知

契約ゲート成功後にSKIP以外で停止する場合（TIMEOUT、Stage・検証・公開・通知の失敗、§1のコード起因の停止）、終了前に次を実行し、送信可否に関わらず当初の非ゼロ終了と失敗報告を維持する。

```text
python scripts/notify_failure.py --stage <停止stage> --reason "<一文>"
```

`<S>` 確定済みなら `--session <S>` を、validator残があれば `--repair-targets .work/<S>/research/repair_targets.json` を付ける。

この通知はコード側でも強制される。`SessionEnd`／`StopFailure` フックが `.claude/hooks/routine_guard.py` を実行し、ゲートが記録した in-flight セッションが存在するのに `.delivered` センチネルが無い場合、停止stage（未完了stageから判定）を添えて自動で通知する。既に通知済みなら再送しない。したがって上記の明示実行を省いても無音にはならないが、**理由を一文で書けるのは実行中のエージェントだけ**なので、停止条件に当たったら省略せず自分で実行する。

`.delivered` は `publish.py --notify` の送信成功時にコードが書く。stage名は自由に付けてよいが、**完走の判定はこのセンチネルだけを根拠にする**。

各stage境界の `stage start|end` は `routine-status` ブランチへ実行位置をpushする（best-effort）。これは基盤都合でセッションが強制終了され、フックすら走らない場合に外部watchdogが「どのstageで止まったか」を知る唯一の経路である。push失敗は警告のみで配信を止めない。

## 7. 非公開記録の保存

`TSE_PRIVATE_STATE_DIR` に永続的な非公開ボリュームを指定する。未設定なら `.work/private` に保存されるため、環境終了前に `python -m tse_ranking_monitor private export --destination <非公開の新規保存先>` で書き出し、実行環境の外へ退避する。次回は保存したディレクトリを復元してから通知する。送信台帳を失うと二重送信防止も失われる。根拠ZIPは180日保持を目安とし、`private retention` で期限切れ一覧を確認できる。削除には運用者の承認後に `--apply` を付ける。送信台帳は保持する。
