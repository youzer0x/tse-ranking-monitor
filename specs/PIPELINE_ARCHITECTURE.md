# 日次生成パイプライン・内部アーキテクチャ

Claude Routine のセッション消費を抑えつつ、ランキングと市場分析の品質を維持するための内部設計である。公開するランキングJSONと市場分析JSONはともに既存の `schema_version=1` を維持する。本書の `*.v1` は `.work/<SESSION>/` 内だけで使う非公開契約である。

## 設計判断

- 1銘柄1エージェントとrow全文のプロンプト貼付を廃止し、決定的な前処理で最大5銘柄のコンパクトなバッチを作る。
- 同じ開示、株探記事、セクタークラスタはプラン生成時に正規化し、バッチ内で一度だけ渡す。
- 調査結果を `evidence.v1` に固定し、ランキング要因と市場分析の値上がり側で再利用する。
- 親オーケストレーターがスキーマ、全コード充足、材料窓、出典、横断因果を検証する。サブエージェントは調査と下書きだけを担う。
- 再試行は失敗したバッチまたは検証ruleが指す銘柄だけに限定し、完了バッチは `input_digest` で再利用する。
- 市場分析はbest-effortのまま維持し、決定的な `market_brief.v1` を境界にしてナラティブへ渡す情報を絞る。

## データフロー

```text
catch-up gate -> Stage1 ranking + market stats
              -> research_plan.v1 / research_batch.v1
              -> 3–5銘柄の調査エージェント群
              -> research_batch_result.v1
              -> evidence.v1 + factors.json
              -> merge_factors.py -> ranking validator
              -> market_brief.v1 -> narrative -> market schema v1 -> market validator
              -> publish -> mainへpush -> Pages digest照合 -> Gmail
```

`ranking.json` は手編集せず、共有正本の `merge_factors.py` だけで更新する。`evidence.v1` から生成する `factors.json` は既存形式なので公開・描画処理を変更しない。

## 内部契約

### research_plan.v1

`scripts/build_research_plan.py` がランキングから生成するmanifestである。材料窓内、窓より前、当日引け後を決定的に分類し、引け後は調査入力から除外する。TDnetと重複する見出しを除き、窓より前の記事は最大2件に制限する。route/riskとクラスタを使って最大5銘柄へ決定的に分割する。

各manifest entryはバッチパス、結果パス、対象コード、route/risk、`input_digest`、チェックポイント状態を持つ。親がサブエージェントへ渡すのはバッチパスとIDだけであり、JSON本文は貼り付けない。

### evidence.v1

`scripts/compile_research_results.py --strict` が全バッチ結果を検証して原子的に生成する。各銘柄は `factor`、3区分の `factor_kind`、確度、claimsとsource ID、材料窓、5パスの実施状態、`market_note` を持つ。コード重複、欠落、digest不一致、無効URL、未定義の列挙値があればstrictでは `evidence.json` と `factors.json` の双方を書かない。

### market_brief.v1

`scripts/build_market_brief.py` がranking、evidence、market_statsから作る。値上がり側の重複銘柄はaccepted evidenceを100%再利用し、追加調査しない。値下がり側の文脈、セクター寄与、breadth、乖離候補と、それぞれの出典IDだけをナラティブ入力に残す。

## 実行資源と再試行

- 日次30銘柄は最大8エージェント、50銘柄replayは最大12エージェントを上限とする。
- 通常バッチは最大5銘柄。深掘りrouteまたは高riskは小さく分割する。
- 親Routineと調査エージェントは初期運用ではSonnet・effort=maxを維持する。モデル変更は構造削減の実測後に別途A/B評価する。
- バリデータは `{path,rule_id,severity,message}` のfindingを出し、親は該当コードを含むバッチだけを最大2回再調査する。
- サブスクリプション内実行を前提とし、API課金や外部クレジットへの自動フォールバックは行わない。

## 観測と受入基準

`.work/<SESSION>/telemetry/events.jsonl` にsession、subagent、tool、failure、compactイベントを追記する。実トークン値がhook payloadにあれば記録し、無ければ入力・出力文字数をproxyとして用いる。生のプロンプト、調査本文、認証情報は記録しない。

構造受入は次を満たすこととする。

- 30銘柄で8エージェント以下、50銘柄replayで12以下。
- 旧方式の静的LLM入力文字数に対して70%以上削減。
- ランキングと重なる値上がりmoverのevidence再利用率100%。
- 空のfactorが0、品質validatorのERROR/WARNが0、ブラインドレビューで品質劣化なし。
- 運用10営業日で利用上限到達0、所要時間p50 35分以下・p95 50分以下。

セッション上限の直接原因はClaude側の利用量ログなしには断定しない。上記telemetryにより、エージェント数、再送文字数、再試行、compact回数と上限到達の関係を検証する。

## 初期replay（2026-07-14）

実データ50行では12バッチ、先頭30行では8バッチとなり、いずれも上限内であった。50行の全batch入力は66,951 bytes（最大batch 13,820 bytes）で、旧方式の約268k文字推計から75.0%減である。材料窓分類により、引け後22件とTDnet重複252件を除外し、窓前記事は225件から82件へ縮約した。

Scheduled Routineの貼り付けプロンプトは6,925文字から851文字へ87.7%減らし、日次に読むhash検証済みruntime contractは3,390文字に収めた。これらは構造的な無駄の存在を支持するが、2026-07-14/15の上限到達そのものの直接因果を証明するものではない。運用10営業日のtelemetryで最終判定する。
