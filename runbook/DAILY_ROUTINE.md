# 日次ルーチン・ランキング品質仕様

東証 日中（レギュラー）値上がり率ランキングを**日次・無人**で生成し、**GitHub Pages（Web）＋ Gmail 通知**で配信する Claude クラウドルーチンの仕様。本リポジトリ `tse-ranking-monitor` の日次ルーチンである（PTS 版 `pts-ranking-monitor` と同じ分担）。

## 方法論の単一の真実源

- 方法論（抽出条件・時価総額算出・厳密窓・変動要因の裏取り・文体・品質ゲート）は
  **`vendor/tse-ranking-digest/SKILL.md`** が単一の真実源。本ファイルはそれに準拠する。
- データ取得系の共有スクリプト（`jquants.py`・`business_day.py`・`kabutan_pts.py`・`tdnet.py`・
  `market_cap_*.py`・`merge_factors.py`）とサブエージェント定義
  （`.claude/agents/stock-factor-researcher.md`）の**コード**は共有リポ **`market-scripts-common`** が
  単一の真実源（ベンダリング。各配布先の `vendor.lock.json` 参照・直接編集禁止）。
- 配信実装（Pages の体裁・Gmail）は on-disk の `tdnet-monitor`（`html_generator.py`・`gmail_sender.py`・`docs/`）を下敷きにしている。

## 起動とゲート

- **cron：当日 16:35 JST**（`scripts/wait_for_data.py` が未処理の最古営業日を選ぶ適応型ゲート）。核ランキングの**唯一の必須依存は対象日の `/equities/bars/daily`**。当日分は確定まで待ち、打ち切りは18:10 JSTとする。manifestより新しい処理漏れ営業日がすでに15:30を過ぎている場合は、その過去日をcatch-up対象として一度だけ鮮度確認し、壁時計待機をしない。
- **営業日ゲート＋鮮度ガード**：`wait_for_data.py` が `docs/data/manifest.json` の最新公開日とJST時刻から対象日を決める。処理対象が無ければ `SKIP`、確定済みなら `SESSION=`、当日データが締切までに未到達またはcatch-upデータが不整合なら `TIMEOUT` とし**配信しない**。Stage1 自身も件数比・masterカバー率・日付整合を検証する。
- 使用モデル：Sonnet 4.6・effort=max（PTS ルーチンに合わせる）。

## フロー

1. **契約とゲート**：`python tools/runtime_contract.py check --contract runbook/runtime_contract.lock.json` を最初に実行し、不一致なら停止する。続いて `python scripts/wait_for_data.py` を実行する。`SKIP` なら更新せず終了、`SESSION=YYYY-MM-DD` ならその日付を使い、`TIMEOUT` なら生成・配信せず原因を報告する。当日待機と過去日catch-upの別、待機時間、WARNを最終報告に含める。
2. **Stage1（決定的）**：`build_day_ranking.py --date <today> --kabutan-news --out ranking.json` を実行（`JQUANTS_API_KEY` 必須）。
   - 抽出条件：東証個別株のみ／値上がり率≥+5%／売買代金≥¥10M／時価総額≥100億。`rows`/`dropped_turnover`/`dropped_mcap` を得る。
   - **掲載上限＝値上がり率上位30社**（該当が30社超なら上位30社のみ `rows` に入る。`--max-rank` 既定30）。`counts.qualifying`＝該当総数、`counts.ranked`＝掲載数。
   - 各 row に **`sector_cluster`**（同一33業種＝`S33` で当日ともに上昇した co-mover ＋ leader 候補）が付き、トップレベルに **`theme_clusters`**（クラスタ要約）が入る（Stage2 のセクター連動クロスチェックに使う）。
   - **`--kabutan-news`** を付けると各 row（上位30社）の **`kabutan_news[]`**（株探 材料・特集〔レーティング日報〕・5%ルール等の直近見出し＋時刻。テクニカル定型ノイズは除外）が事前充填され、Stage2 で「材料未確認」へ落とす前の確認材料になる。best-effort（失敗時は空配列）。
3. **Stage2（計画→バッチ調査→evidence）**：`python scripts/build_research_plan.py --ranking .work/<SESSION>/ranking.json --out-dir .work/<SESSION>/research` を実行する。決定的前処理が材料窓外・引け後の見出しを分離し、TDnet重複を除き、クラスタを一度だけ正規化して最大5銘柄の `research_batch.v1` を作る。
   - manifestで `pending` の各バッチを `.claude/agents/tse-factor-batch-researcher.md` に委譲する。タスク本文は `batch_id` と `batch_path` のみとし、rowやplan本文を貼らない。結果JSONをmanifestの `result_path` へ親が原子的に保存する。
   - 親は `python scripts/compile_research_results.py --research-dir .work/<SESSION>/research --strict` を実行する。欠落、重複、digest不一致、材料窓・出典・5パスの契約違反があれば出力せず、該当バッチだけ最大2回再調査する。成功時は `evidence.v1` と既存形式の `factors.json` を得る。
   - `python scripts/merge_factors.py --ranking .work/<SESSION>/ranking.json --factors .work/<SESSION>/research/factors.json` で反映する。`ranking.json` は手編集しない。
   - 親オーケストレーターが全行と `theme_clusters` を横断検証する。異質な33業種を機械的に同一テーマへ結ばず、開示内容、時系列、定量寄与、代替要因が揃う範囲だけを帰属する。修正はbatch resultまたはfactorsへ戻し、compile/mergeを再実行する。
   - `python scripts/validate_ranking_quality.py .work/<SESSION>/ranking.json --evidence .work/<SESSION>/research/evidence.json --format json --repair-targets .work/<SESSION>/research/repair_targets.json` を実行し、findingが指すコードだけを修復する。空のfactor、ERROR、未対応WARNを残したまま公開しない。詳細な出典・因果規律は本書後半とvendor正本に従う。
3.5. **市場分析タブのデータ生成（best-effort・ランキング配信をブロックしない）**：ランキングと**同一の push** に載せるため Publish（step4）の前に生成する。16:35 起動では当日 `/fins/summary`（速報~18:00頃）は未反映だが、**市場分析タブに表示されるのは bars/master/topix 由来の要素のみ**（セクター騰落・breadth・TOPIX・movers 表）で、当日決算開示は配信物に出ないため影響しない。手順の詳細は `specs/MARKET_ANALYSIS.md` に従う。要点のみ：
   - **(a) 決定的データ**：`python scripts/build_market_stats.py --date <SESSION> --out-dir .work/<SESSION>/market`。`.work/<SESSION>/market/` に `sector_return_<SESSION>.csv`・`movers_top_<SESSION>.csv`（sector_analysis.py 移植版）と `market_stats_<SESSION>.json`（TOPIX 前日比・breadth・最大代金セクター/銘柄〔全ユニバース真値〕・セクター騰落率表「銘柄」列の主導銘柄 `sector_drivers`〔寄与順1〜2銘柄〕・**⚠乖離フラグ候補 `divergence_flags`**〔執筆ヒント〕・movers の TDnet 開示文脈 `movers_context`）を出力。
   - **(b) 根拠パック**：`python scripts/build_market_brief.py --ranking .work/<SESSION>/ranking.json --evidence .work/<SESSION>/research/evidence.json --stats .work/<SESSION>/market/market_stats_<SESSION>.json --out .work/<SESSION>/market/market_brief_<SESSION>.json`。ランキングと重なる値上がりmoverはaccepted evidenceをそのまま再利用し、再調査しない。値下がり側の追加調査はbriefの選定銘柄だけに限定する。
   - **(c) ナラティブ・フラグメント執筆**：`market_brief.v1` だけを根拠パックとして `.work/<SESSION>/market/narrative_<SESSION>.json`（**コミットしない**）を `specs/MARKET_ANALYSIS.md` の品質要件で執筆する。
   - **(d) 結合**：`python scripts/build_market_json.py --date <SESSION> --csv-dir .work/<SESSION>/market --stats .work/<SESSION>/market/market_stats_<SESSION>.json --defaults scripts/market_fragment_defaults.json --narrative .work/<SESSION>/market/narrative_<SESSION>.json --out docs/data/<SESSION>_market.json`。
   - **(e) 品質検証**：`python scripts/validate_market_quality.py docs/data/<SESSION>_market.json --format json --repair-targets .work/<SESSION>/market/repair_targets.json`。findingのpath/ruleだけを修復して(c)〜(e)を最大2回再実行する。本文を削って通さず、briefのsource IDを第一に再利用する。
   - **失敗時**：(a)〜(e) のどこで失敗しても市場分析は**スキップして step4 へ進む**（`docs/data/<SESSION>_market.json` が無くても SPA はタブ empty 表示に自然退避する。**ランキング配信は成功として扱う**）。`.work/<SESSION>/` はコミットしない。
4. **Publish（生成のみ・メールは送らない）**：`publish.py --in .work/<SESSION>/ranking.json --docs docs --pages-url "$PAGES_URL"`
   - `docs/data/<date>.json` 保存（ランキング＋要因）／`docs/data/manifest.json` 更新／30日より古い JSON を削除。
   - `docs/index.html`（日付選択式 Pages）を更新（体裁は `html_generator.py`＝PTS 版と同一トンマナ・配色）。保存 JSON は rows に開示（pdf_url）を含むフルデータ。
   - メールは送信せず、再生成可能なメールHTMLも公開保存しない。通知時に公開済みランキングJSONから本文を生成する。
5. **デプロイ（必ず main へ）**：`docs/index.html` と `docs/data/` を commit し、`git push origin HEAD:main`。
   `docs/data/` には step4 のランキング JSON に加え、step3.5 が成功していれば `<SESSION>_market.json`（市場分析）も含まれ、**同一 push** で配信される（`git add docs/index.html docs/data` が両方を拾う）。
   GitHub Pages は **main/docs** を配信するため、クラウドが `claude/` ブランチ上にいても **main へ直接 push**する（PR は作らない。リポジトリは unrestricted branch push 許可）。`.work/<SESSION>/` はコミットしない。
6. **メール通知（Pages 反映後に送信）**：`publish.py --in .work/<SESSION>/ranking.json --docs docs --pages-url "$PAGES_URL" --notify`
   - **GitHub Pages 上の当日ランキングartifact digestがローカル公開物と一致する**まで、manifestとランキングJSONを
     キャッシュ無効化付きで**最大5分ポーリング**し、一致確認後にメール HTML を **Gmail API（HTTPS）送信**（`gmail_sender.send_gmail`）。
     クラウド環境は SMTP(465) を通さないため **PTS 版と同じ Gmail API 方式**を用いる。必要な環境変数は
     `GMAIL_CLIENT_ID`／`GMAIL_CLIENT_SECRET`／`GMAIL_REFRESH_TOKEN`／`GMAIL_ADDRESS`／`NOTIFY_TO`
     （リフレッシュトークンは `scripts/get_gmail_token.py` でローカル1回取得。`runbook/SETUP.md` 参照）。
   - **必ず step5 の push の後**に実行する。push 前にメールを送ると、読者がリンクを開いた時点で Pages が
     まだ前コミット（最新日付＝前営業日）を返し、当日分が見えない（=メールのリンク先ラグ）。`--notify` はその窓を閉じる。
   - ライブ確認の取得先は Pages ホスト（`*.github.io`）。ルーチンのカスタム環境の**ネット許可に `*.github.io` を含める**こと
     未許可・5分以内にdigest不一致・認証不足・Gmail API失敗はいずれも**未送信のまま非ゼロ終了**する。`--notify` は生成・コミットを行わない（step4/5 済み前提）。

## レポート（任意）

- オンデマンド版と同じ `である調` 全文を `reports/tse-rankings/<date>_tse-gainers.md` 相当として併せて出力してよい（リポ運用に合わせる）。

## 変動要因の品質規律（step3・機械ゲート `validate_ranking_quality.py`）

ランキングの `factor`/`factor_kind` は市場分析タブと**同一基準**でファクトチェックする。`validate_ranking_quality.py`
が step3 の充填後に機械検査し（因果語・精密主張トリガー・禁止URL・推定マーカーの語彙は `validate_market_quality.py`
から import 再利用＝唯一の真実源）、**内容の充実度を落とさず正確性を担保する**（削らず出典を足す・再タグ・推定表現化）。
市場分析の「要因帰属の規律」（`specs/MARKET_ANALYSIS.md`＝共起≠因果・文体の階層）を変動要因にも適用する。恒久ルール：

- **開示タグは窓内 TDnet 開示に厳格化（機械検査＝ERROR）**：`factor_kind=開示` は「前営業日15:30以降∪当日15:30未満」
  の TDnet 適時開示が `disclosures[]` に在るときのみ使う。**証券会社のレーティング変更・EDINET の大量保有/TOB は
  TDnet に出ない＝`[報道]` とし、一次URL（会社IR・EDINET 書類・当該事実を最初に報じた記事）を本文に明記**する。
  窓外・継続材料は `[テーマ]` とし**起点報道の日付を本文に記す**（日付の無い「〜報道を受け」は禁止）。
- **開示の材料日付を disclosures と整合させる（機械検査＝WARN）**：`開示` の本文に挙げる日付は `disclosures[].date`
  または当日/前営業日と一致させる（例：開示は7/1なのに本文が「6/30開示」＝材料日付のドリフト。監査 §2 の再発防止）。
- **無出典の因果・直接材料帰属・具体報道の断定を避ける（機械検査＝WARN）**：断定的な因果語（点火・波及・誘発・
  押し上げ・主導・直接受益）／直接材料の帰属（「決算/報道/開示/発表を受け」「材料視」）／外部報道の断定
  （「〜との報道」「検討が浮上」等）は、一次開示（開示タグ＋`disclosures`）・本文の出典リンク・`kabutan_news`・
  推定マーカー（連想・とみられる・連れ高・並走 等）のいずれかを伴わせる。伴わないなら**起点報道日と記事URLを
  本文に明記**するか推定表現にとどめる（岡野バルブ誤帰属の再発防止。愛三工業型の実世界事実の誤り＝過年度の
  関税合意を当日材料化する等は機械検査では捕捉できないため、窓内材料の裏取りで人手確認する）。
- **報道タグの精密イベントを裏付ける（機械検査＝WARN）**：`[報道]` で目標株価・TOB・上方/下方修正・格上げ/格下げ・
  大量保有・公開買付・非公開化 を挙げるなら `disclosures`・`kabutan_news`・本文の出典リンクのいずれかで裏付ける。
- **factor_kind の空欄・既定外を残さない（機械検査＝WARN）**：空欄・`確認不可` 等は不可。材料未確認は5パス確認後に
  「当日固有の材料は確認できず（5パス確認済み）」等と**非空テキスト**で正直に記す（監査 §4 の空欄・確認不可の解消）。
- **禁止ランディングページを出典にしない（機械検査＝ERROR）**：`disclosures[].pdf_url`・本文リンクに株探/みんかぶ/
  日経会社情報/Yahoo!quote の銘柄ページを使わない（`specs/MARKET_ANALYSIS.md` の「ランディングページ出典禁止」と同一）。
- **本文の出典リンク**：`factor` 内に `[出典名](URL)` を書けば Pages・メールとも自動でリンク描画される
  （`html_generator.py` の SPA `mdInline`／メール `_factor_html`。`開示` の窓内開示は従来どおり `[開示PDF]` を自動付与）。
  これで「削らず出典を足す」修復が変動要因でも成立する。


## 関連

- 方法論：`vendor/tse-ranking-digest/SKILL.md`
- 配信下敷き：`project-private/tdnet-monitor`（Pages＋Gmail）
- ルーチン方式の先行例：`pts-ranking-monitor`（PTS ナイト版・cron 06:06 JST）
