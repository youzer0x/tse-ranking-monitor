# 日次生成パイプライン・内部アーキテクチャ

Claude Routine のセッション消費を抑えつつ、ランキングと市場分析の品質を維持するための内部設計である。公開するランキングJSONと市場分析JSONはともに既存の `schema_version=1` を維持する。本書の `*.v1` は `.work/<SESSION>/` 内だけで使う非公開契約である。

## 設計判断

- 1銘柄1エージェントとrow全文のプロンプト貼付を廃止し、決定的な前処理で最大5銘柄のコンパクトなバッチを作る。
- 同じ開示、株探記事、セクタークラスタはプラン生成時に正規化し、バッチ内で一度だけ渡す。
- M&A関連銘柄の単独バッチ化は2026-07-16に廃止し、高リスク枠（最大3件プール）へ統合した。根拠とされ得た誤帰属事故（2026-07-03 岡野バルブ等、`audits/2026-07-05-factor-quality.md` §2）はバッチ・プーリング導入前の1銘柄1エージェント体制で発生しており単独化の効果を裏付けず、SKILL.mdのEDINET確認義務（M&Aまたは出来高急増小型株）とも精密主張規律（TOB以外も対象）ともスコープが不整合だった。EDINET確認義務は銘柄単位の `requires_edinet` フラグで伝達し、strict compileが `edinet=na` を機械拒否する。カテゴリ単位の特別扱いを再導入する場合は、実データによる効果の裏付けと本specの改訂を必須とする。
- 「出来高急増の小型株」へのEDINET確認義務（SKILL.md 5パス目）は、現行入力に出来高履歴・時価総額が無いため機械判定せず、プロンプト水準の義務として残る（既存ギャップ。運用10営業日telemetryレビューで機械化を再検討する）。
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

- dispatch予算はmanifestの `dispatch_budget`（初期上限12・総予算18・バッチ毎3）が正であり、プランナーが機械判定する。バッチ数の理論上限は `ceil(H/3)+ceil(D/3)+ceil(N/5)`（H=高リスク・D=通常深掘り・N=通常直接）で、日次30銘柄の最悪は12（例: H1+D28+N1）＝初期上限内。12超過（catch-up/replay等の30件超入力）は `build_research_plan.py` が非ゼロ終了する。
- 全ての委譲（初回・compile再試行・validator修復・再開）は委譲直前に `reserve_dispatch.py` でmanifestへ原子的に予約し、予約なき委譲を行わない。checkpoint済みバッチは予算を消費しない。
- 通常バッチは最大5銘柄。深掘りrouteまたは高riskは小さく分割する。
- 親Routineと調査エージェントは初期運用ではSonnet・effort=maxを維持する。モデル変更は構造削減の実測後に別途A/B評価する。
- バリデータは `{code,path,rule_ids,severities,messages}` のrepair targetを出し、`repair_research_plan.py` が指摘全文と旧結果の要点（対象=修正ベース `previous`、非対象=再掲用 `carry_forward`）を該当バッチへ注入して `input_digest` を再計算し `pending` に戻す（バッチごと最大2回）。carry_forwardの改変と `requires_edinet` 行の `edinet=na` はstrict compileが拒否する。完了バッチは `input_digest` で再利用し、digest不一致の旧resultは `.stale` へ隔離して `pending` に再キューする。
- サブスクリプション内実行を前提とし、API課金や外部クレジットへの自動フォールバックは行わない。

## 観測と受入基準

`.work/<SESSION>/telemetry/events.jsonl` にsession、subagent、tool、failure、compactイベントを追記する。実トークン値がhook payloadにあれば記録し、無ければ入力・出力文字数をproxyとして用いる。生のプロンプト、調査本文、認証情報は記録しない。

構造受入は次を満たすこととする。

- `dispatch_budget` の判定が真（初期pending 12以下）であり、総予約が18以下で完走する。
- 旧方式の静的LLM入力文字数に対して70%以上削減。
- ランキングと重なる値上がりmoverのevidence再利用率100%。
- 空のfactorが0、品質validatorのERROR/WARNが0、ブラインドレビューで品質劣化なし。
- 運用10営業日で利用上限到達0、所要時間p50 35分以下・p95 50分以下。

セッション上限の直接原因はClaude側の利用量ログなしには断定しない。上記telemetryにより、エージェント数、再送文字数、再試行、compact回数と上限到達の関係を検証する。

## 初期replay（2026-07-14）

実データ50行では12バッチ、先頭30行では8バッチとなり、いずれも上限内であった。50行の全batch入力は66,951 bytes（最大batch 13,820 bytes）で、旧方式の約268k文字推計から75.0%減である。材料窓分類により、引け後22件とTDnet重複252件を除外し、窓前記事は225件から82件へ縮約した。

Scheduled Routineの貼り付けプロンプトは6,925文字から851文字へ87.7%減らし、日次に読むhash検証済みruntime contractは3,390文字に収めた。これらは構造的な無駄の存在を支持するが、2026-07-14/15の上限到達そのものの直接因果を証明するものではない。運用10営業日のtelemetryで最終判定する。
