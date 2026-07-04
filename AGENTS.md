# tse-ranking-monitor（自動化ルーチン仕様）

東証 日中（レギュラー）値上がり率ランキングを**日次・無人**で生成し、**GitHub Pages（Web）＋ Gmail 通知**で配信する Claude クラウドルーチンの仕様。本ディレクトリは独立リポ `tse-ranking-monitor` として切り出すための**雛形**である（PTS 版 `pts-ranking-monitor` と同じ分担）。

## 方法論の単一の真実源

- 方法論（抽出条件・時価総額算出・厳密窓・変動要因の裏取り・文体・品質ゲート）は
  **`news-financial-market/skills/tse-ranking-digest/SKILL.md`** が単一の真実源。本ファイルはそれに準拠する。
- データ取得系の共有スクリプト（`jquants.py`・`business_day.py`・`kabutan_pts.py`・`tdnet.py`・
  `market_cap_*.py`・`grok_research.py`）の**コード**は共有リポ **`market-scripts-common`** が
  単一の真実源（`scripts/` へベンダリング。`scripts/vendor.lock.json` 参照・直接編集禁止）。
- 配信実装（Pages の体裁・Gmail）は on-disk の `tdnet-monitor`（`html_generator.py`・`gmail_sender.py`・`docs/`）を下敷きにしている。

## 起動とゲート

- **cron：当日 16:35 JST**（`scripts/wait_for_data.py` が当日四本値の反映を待つ適応型ゲート）。核ランキングの**唯一の必須依存は当日 `/equities/bars/daily`（四本値・公式反映「16:30頃」・実際は前後）**。`/equities/master` は当日分を日中取得可（17:30 制約は"翌営業日"マスタで非ボトルネック）、時価総額の `/fins/summary` は前期末確定株数で足り、財務速報 18:00 は配信物に不使用。打ち切りは 18:10 JST 壁時計で「現状より遅くしない／現行が配信できた日を取りこぼさない」を保証。`reference-jquants-data-update-timing`。
- **営業日ゲート＋鮮度ガード**：`wait_for_data.py` が休場日を即 `SKIP`（ネット無し）、営業日は当日四本値が確定するまで待って `SESSION=` を出す。締切までに未到達なら `TIMEOUT`＝**配信しない**（`build_day_ranking.py` に空/部分データの自己防御が無いためのフェイルセーフ）。
- 使用モデル：Sonnet 4.6・effort=max（PTS ルーチンに合わせる）。

## フロー

1. **ゲート（鮮度ガード付き）**：`python scripts/wait_for_data.py` を実行。`SKIP`（休場）なら Pages もメールも更新せず即終了。`SESSION=YYYY-MM-DD`（当日四本値の確定を待って出力。**呼び出しは待機で数分ブロックしうる＝正常**）ならその日付を SESSION として続行。`TIMEOUT`（締切 18:10 JST までに当日四本値が未到達）なら**生成・配信せず**、J-Quants の遅延/障害として最終報告する。準完全で続行した場合は stderr の `WARN` を最終報告に含める。
2. **Stage1（決定的）**：`build_day_ranking.py --date <today> --kabutan-news --out ranking.json` を実行（`JQUANTS_API_KEY` 必須）。
   - 抽出条件：東証個別株のみ／値上がり率≥+5%／売買代金≥¥10M／時価総額≥100億。`rows`/`dropped_turnover`/`dropped_mcap` を得る。
   - **掲載上限＝値上がり率上位50社**（該当が50社超なら上位50社のみ `rows` に入る。`--max-rank` 既定50）。`counts.qualifying`＝該当総数、`counts.ranked`＝掲載数。
   - 各 row に **`sector_cluster`**（同一33業種＝`S33` で当日ともに上昇した co-mover ＋ leader 候補）が付き、トップレベルに **`theme_clusters`**（クラスタ要約）が入る（Stage2 のセクター連動クロスチェックに使う）。
   - **`--kabutan-news`** を付けると各 row（上位50社）の **`kabutan_news[]`**（株探 材料・特集〔レーティング日報〕・5%ルール等の直近見出し＋時刻。テクニカル定型ノイズは除外）が事前充填され、Stage2 で「材料未確認」へ落とす前の確認材料になる。best-effort（失敗時は空配列）。
2.5. **（任意・既定 off）grok 委譲リサーチ**：env **`TSE_USE_GROK=1`** のときのみ実行。
   `python scripts/grok_research.py --in docs/tmp/ranking.json --out-dir docs/tmp/research --top 25` を実行し、
   **上昇率上位25社**の変動要因を **xAI Grok API**（`XAI_API_KEY`・web_search ツール）でリサーチ → `<code>-<name>-<date>.md`（末尾に DIGEST_BLOCK）を生成する（**APIコスト削減方針：grok は上位25社まで。26位以降は Stage2 で Claude〔手順B〕が裏取り**）。
   `TSE_USE_GROK` 未設定/0 のときは本ステップを**完全にスキップ**（従来の Claude 完結フローと同一）。`grok_research.py` がエラー・API 失敗のときも、その回は grok を捨てて Stage2 を全行 Claude で実施する。
   方法論・プロンプトは `skills/tse-ranking-digest/grok/`（共有雛形）・`reference/sources.md §4`（3層ソース方針）に準拠。
3. **Stage2（変動要因の充填）**：`rows`（上位50社）各銘柄の `factor`/`factor_kind` を
   **[開示]（TDnet 前営業日15:30以降∪当日15:30未満）→[報道]（一次記事＋配信時刻を当日セッションに整合）→[セクター連動クロスチェック]→[テーマ]** の順で埋める。
   検索要約を出典にせず、材料未確認は5パス確認後にのみ正直に記す（詳細は `SKILL.md §5 手順B`）。
   - **`TSE_USE_GROK=1` のとき（手順B'）**：Stage2.5 の `docs/tmp/research/<code>-<name>-<date>.md` を `code`×`session_date` で取り込む。
     **研究本文を主入力**とし（DIGEST_BLOCK は索引・要約で単独依存しない＝当日ドライバーを取りこぼす）、取り込み時に **(a) ランディングページ出典の全削除（Yahoo `/quote`・日経会社ページ・株探銘柄トップ等）・(b) 数値を J-Quants/一次開示で再検証・(c) 窓外材料は「背景」に格下げ** を行い、
     **3層ソース再検証**（`sources_used`=採用／`sources_new_candidate`=ルーブリック再評価のうえ採用＋whitelist 昇格候補に記録／`sources_excluded`=不採用）＋`window_ok`/`trigger_time` の厳密窓整合を確認して `factor`/`factor_kind` を起こす（**発見は grok・判定は Claude**）。
     **研究ファイル欠落・検証落ち・窓不整合の行は従来の Claude 裏取りに fallback**。検証合格率が掲載行の半数未満なら grok を捨てて全行 Claude。
   - **ソース規律（3層方針）**：①中核 whitelist は採用、②良質な非whitelist（フィスコ・みんかぶ編集記事等）はルーブリック合格なら採用、③個人発信・匿名・純アルゴ生成は不使用（`reference/sources.md §4`）。
   - **証券会社のレーティング変更（投資判断・目標株価）も必ずカバーする**。TDnet には出ないため、`disclosures` が空なのに日中上昇した銘柄は **株探の銘柄ニュース `https://kabutan.jp/stock/news?code=<4桁>`（ブラウザ UA）の「レーティング日報」「材料」**を確認する（`--kabutan-news` 実行時は各 row の `kabutan_news[]` に充填済み）。寄り前に出た格上げ・目標株価引き上げ（当日15:30より前に伝わったもの）は日中上昇の有力材料。証券会社名・旧→新の投資判断/目標株価を具体的に記し、区分は `[報道]`。
   - **セクター連動クロスチェック（必須）**：各 row の `sector_cluster`／トップレベル `theme_clusters` を読み、同一33業種で束で動いた銘柄は、クラスタ内で具体的[開示]を持つ銘柄（`has_disclosure=true`。`leader_code` は機械的ヒントなので開示内容を読んで真の牽引役を選び直す）を名指しで根拠化し `[テーマ]`（連鎖）として帰属する。業種をまたぐテーマ（例：光部品＝非鉄金属〔電線〕＋電気機器〔光部品〕＋精密機器）は各クラスタの leader と地合いから横断的に結ぶ。**同一テーマの co-mover を材料未確認で放置しない**。連鎖／継続（決算後ドリフト）／需給（前日反動・薄商い）の別は本文で書き分ける（区分は3タグ維持）。
   - **材料未確認ゲート**：「材料未確認」は (i)`disclosures` (ii)`kabutan_news`（株探材料/レーティング） (iii)Web 検索 `<コード> <銘柄名> 急騰/ストップ高` (iv)`sector_cluster` の leader 連動 (v)（M&A観・出来高急増の小型株は）EDINET の TOB/大量保有 を**すべて確認**してもなお当日窓に材料が無い行にのみ残す。**ペイウォールで本文が取れないだけで即材料未確認にしない**（他の①②媒体で二次裏取り→当日テーマへの帰属が成れば [テーマ]）。
   - **EDINET（任意・環境依存）**：EDINET DB MCP が利用可能な環境では、買収観・大量保有が疑われる行で TOB（公開買付届出）・大量保有報告書を一次情報として確認する。MCP が無い環境では本パスは任意で、既存パスへフォールスルーする（**ハードな依存にしない**）。
3.5. **市場分析タブのデータ生成（best-effort・ランキング配信をブロックしない）**：ランキングと**同一の push** に載せるため Publish（step4）の前に生成する。16:35 起動では当日 `/fins/summary`（速報~18:00頃）は未反映だが、**市場分析タブに表示されるのは bars/master/topix 由来の要素のみ**（セクター騰落・breadth・TOPIX・movers 表・乖離フラグ）で、当日決算開示は配信物に出ないため影響しない。手順の詳細は §「市場分析タブ（日次自動生成）」に従う。要点のみ：
   - **(a) 決定的データ**：`python scripts/build_market_stats.py --date <SESSION> --out-dir docs/tmp/market`。`docs/tmp/market/` に `sector_return_<SESSION>.csv`・`movers_top_<SESSION>.csv`（sector_analysis.py 移植版）と `market_stats_<SESSION>.json`（TOPIX 前日比・breadth・最大代金セクター/銘柄〔全ユニバース真値〕・**⚠乖離フラグ候補 `divergence_flags`**・movers の TDnet 開示文脈 `movers_context`）を出力。
   - **(b) ナラティブ・フラグメント執筆**：`docs/tmp/market/narrative_<SESSION>.json`（**コミットしない**）を §「市場分析フラグメント執筆」の品質要件で執筆する。
   - **(c) 結合**：`python scripts/build_market_json.py --date <SESSION> --csv-dir docs/tmp/market --stats docs/tmp/market/market_stats_<SESSION>.json --defaults scripts/market_fragment_defaults.json --narrative docs/tmp/market/narrative_<SESSION>.json --out docs/data/<SESSION>_market.json`。バリデーション die はフラグメントを直して**最大2回**再実行。
   - **(d) 品質検証**：`python scripts/validate_market_quality.py docs/data/<SESSION>_market.json`。出典品質（news_sources/emph movers の links 空・ランディングページ URL・精密主張のリンク欠落・同一URLの重複掲載）を検査する。非ゼロ終了なら**出典を足して**（Stage2 の採用出典・`kabutan_news`・TDnet/EDINET の再利用が第一手。**本文の削除・弱体化で通すことを禁止**）フラグメントを直し、(b)〜(d) を最大2回再実行。裏取り探索を尽くしても出典が無い主張のみ数値を外して弱め、最終報告に理由を残す。
   - **失敗時**：(a)〜(d) のどこで失敗しても市場分析は**スキップして step4 へ進む**（`docs/data/<SESSION>_market.json` が無くても SPA はサマリー帯非表示・タブ empty に自然退避する。**ランキング配信は成功として扱う**）。`docs/tmp/` はコミットしない。
4. **Publish（生成のみ・メールは送らない）**：`publish.py --in docs/tmp/ranking.json --docs docs --pages-url "$PAGES_URL"`
   - `docs/data/<date>.json` 保存（ランキング＋要因）／`docs/data/manifest.json` 更新／30日より古い JSON を削除。
   - `docs/index.html`（日付選択式 Pages）を更新（体裁は `html_generator.py`＝PTS 版と同一トンマナ・配色）。保存 JSON は rows に開示（pdf_url）を含むフルデータ。
   - メール HTML を生成・保存する**が、この段階では送信しない**（`--send` は付けない。送信は step6）。
5. **デプロイ（必ず main へ）**：`docs/index.html` と `docs/data/` を commit し、`git push origin HEAD:main`。
   `docs/data/` には step4 のランキング JSON に加え、step3.5 が成功していれば `<SESSION>_market.json`（市場分析）も含まれ、**同一 push** で配信される（`git add docs/index.html docs/data` が両方を拾う）。
   GitHub Pages は **main/docs** を配信するため、クラウドが `claude/` ブランチ上にいても **main へ直接 push**する（PR は作らない。リポジトリは unrestricted branch push 許可）。`docs/tmp/` はコミットしない。
6. **メール通知（Pages 反映後に送信）**：`publish.py --in docs/tmp/ranking.json --docs docs --pages-url "$PAGES_URL" --notify`
   - **GitHub Pages が当日 SESSION を実際に配信し始める**（`data/manifest.json` の最新日付＝SESSION になる）まで
     キャッシュ無効化付きで**最大5分ポーリング**し、確認後にメール HTML を **Gmail API（HTTPS）送信**（`gmail_sender.send_gmail`）。
     クラウド環境は SMTP(465) を通さないため **PTS 版と同じ Gmail API 方式**を用いる。必要な環境変数は
     `GMAIL_CLIENT_ID`／`GMAIL_CLIENT_SECRET`／`GMAIL_REFRESH_TOKEN`／`GMAIL_ADDRESS`／`NOTIFY_TO`
     （リフレッシュトークンは `scripts/get_gmail_token.py` でローカル1回取得。setup/SETUP.md 参照）。
   - **必ず step5 の push の後**に実行する。push 前にメールを送ると、読者がリンクを開いた時点で Pages が
     まだ前コミット（最新日付＝前営業日）を返し、当日分が見えない（=メールのリンク先ラグ）。`--notify` はその窓を閉じる。
   - ライブ確認の取得先は Pages ホスト（`*.github.io`）。ルーチンのカスタム環境の**ネット許可に `*.github.io` を含める**こと
     （未許可だと毎回失敗→5分後に送信＝従来同様ラグが残る）。`--notify` は生成・コミットを行わない（step4/5 済み前提）。

## レポート（任意）

- オンデマンド版と同じ `である調` 全文を `reports/tse-rankings/<date>_tse-gainers.md` 相当として併せて出力してよい（リポ運用に合わせる）。

## 市場分析タブ（日次自動生成・**step3.5**）

- ランキング Pages（`docs/index.html`）は、値上がりランキングに加えて **上部の市況サマリー帯**と **「📊 市場分析」タブ**（`#market` ハッシュで開く）を持つ。データは**ランキング JSON とは別ファイル** `docs/data/<date>_market.json`（スキーマ v1）から fetch する（無い日付はサマリー帯非表示・タブは empty 表示に自然退避）。`manifest.json` には**載せない**（`update_manifest()` が `_market` を意図的に除外）。`cleanup_old()` は `<date>_market.json` も同じ30日保持で削除する（`base[:10]` 判定）。
- **二層構成（数値とナラティブの分離）**：数値は決定的スクリプトが、ナラティブは Claude が書き、結合器が機械ジョインする。転記ミスはセクター名・銘柄コードの完全一致バリデーションで構造的に排除される（不一致は非ゼロ終了）。
  1. **決定的データ** `scripts/build_market_stats.py`（`test-jquants` の `sector_analysis.py` を本リポへ移植した無人版。`jquants.py`/`business_day.py`/`tdnet.py` を再利用・stdlib＋`JQUANTS_API_KEY` のみ）。`--out-dir docs/tmp/market` に `sector_return_<date>.csv`・`movers_top_<date>.csv`（33業種加重/単純/中央値・値上/値下上位30）と `market_stats_<date>.json` を出力。stats には CSV 外の決定的数値（`topix_pct`／`prev_date`／`generated_at`／`breadth`／`top_sector_by_turnover`／`top_stock_by_turnover`〔全ユニバース真値〕）と執筆ヒント（`divergence_flags`＝⚠乖離候補・`movers_context`＝movers の TDnet 開示）が入る。
  2. **ナラティブ・フラグメント**（Claude 執筆・`docs/tmp/market/narrative_<date>.json`・コミットしない）：§「市場分析フラグメント執筆」参照。
  3. **結合** `scripts/build_market_json.py`（stdlib のみ・冪等）：`--csv-dir docs/tmp/market --stats <market_stats>.json --defaults scripts/market_fragment_defaults.json --narrative <narrative>.json --out docs/data/<date>_market.json`。`--stats` から `topix_pct`/`prev_date`/`generated_at`/最大代金銘柄/`overview.snapshot` の auto 行を機械採用し、`breadth`・`n_liquid` を CSV と相互チェック（不一致は die）。`--defaults` は静的テンプレ（`title`/`universe`/`methodology`/`disclaimer`・`{n_liquid}` は結合時置換）を供給する。
  4. **品質検証** `scripts/validate_market_quality.py`（stdlib のみ・`validate_market` の構造検証も内包）：出典品質（links 空・禁止 URL・精密主張のリンク有無）を機械検査する。
- 生成は日次フロー **step3.5**（Publish の前）に組み込み済み。**best-effort**（失敗時はスキップして step4 へ・ランキング配信はブロックしない）。公開成果物は `docs/data/<date>_market.json` と再生成した `docs/index.html`。フラグメント・`docs/tmp/` はコミットしない。

## 市場分析フラグメント執筆

`docs/tmp/market/narrative_<date>.json` に**当日ナラティブのみ**を書く（数値・静的テンプレは書かない）。フラグメントはセクター名・銘柄コードのみを指定し、数値は CSV/stats からジョインされる。

- **フラグメントの JSON 形（厳守）**：結合器 `build_market_json.validate_market()` が SPA（`html_generator.py renderMarket`）の要求型を検証し、不一致は**die → 本節の形に直して再実行**（型崩れは配信画面のタブを空にする＝過去に `sector_notes` をオブジェクトにして renderMarket が例外→タブ空という事故があった）。次の**配列/オブジェクトの別を厳守**する：
  ```json
  {
    "thesis": ["…（要点を2〜3件の短文で。文字列1本でも可）"],
    "strip": {"sectors_up": ["セクター名"], "sectors_down": ["セクター名"]},
    "overview": {
      "snapshot": [{"auto": "topix|breadth|top_sector|top_stock"}, {"label": "…", "value": "…", "note": "…"}],
      "points": ["…"], "flow": ["…"], "flow_conclusion": ["…（2〜3件の短文。文字列1本でも可）"]
    },
    "sector_flags": {"セクター名": "⚠"},
    "sector_notes": [{"mark": "⚠1|示唆|セクター名", "text": "…"}],
    "bought": {"table": [{"sector": "…", "note": "…", "flag": "⚠?"}], "themes": []},
    "sold":   {"table": [{"sector": "…", "note": "…"}],              "themes": []},
    "movers": {"gainers": [{"code": "…", "note": "…", "links": [{"label": "…", "url": "https://…"}], "emph": true}],
               "gainers_footnote": "…",
               "losers":  [{"code": "…", "note": "…", "links": [{"label": "…", "url": "https://…"}]}],
               "losers_footnote": "…"},
    "theme_matrix": {"rows": [{"side": "buy|sell", "theme": "…", "stocks": "銘柄名・銘柄名", "background": "…"}], "character": "…"},
    "news_sources": [{"topic": "…", "links": [{"label": "…", "url": "https://…"}]}]
  }
  ```
  特に落とし穴：**`sector_notes` は配列 `[{mark,text}]`（オブジェクト `{セクター名: …}` にしない）／`theme_matrix` はオブジェクト `{rows:[{side?,theme,stocks,background}], character}`（テーマ配列にしない・旧 `bought/sold` キーは廃止）／`overview.points`・`overview.flow` は文字列配列／`thesis`・`overview.flow_conclusion` は文字列 or 文字列配列（配列なら箇条書きで描画）**。`bought`・`sold` の `themes` は原則 `[]`（下記「表示・文体の規約」参照）。`theme_matrix` が無ければ省略（結合器が `{}` にし SPA はテーマ節を出さない）。

- **編集レビュー由来の恒久規約（先回り適用・都度追記）**：市場分析タブはユーザーの編集レビューで書式・文体を確定してきた（2026-07-01/02＝内容・出典・数値の規律〔本節 各項〕、2026-07-03＝下記の表示・文体）。**日次自動生成はこれらを最初から適用**し、レビューで新たに確定した指摘は**その場限りにせず本節へ追記**して同じ指摘を繰り返させない（レビュー確定 → 本節へ恒久ルール化 → 次回以降 先回り適用、が運用規律）。表示・文体の具体規約：
  - **銘柄名の強調**：地の文（`thesis`／`overview.points`・`flow`・`flow_conclusion`／`sector_notes.text`／`bought`・`sold` の `note`／`movers.note`／`theme_matrix.background`）の**個別銘柄名は `[[銘柄名]]` で囲む**（SPA が配色し目立たせる。テーマ別資金フロー行では買い=赤/売り=緑に自動配色）。**表の専用カラム**（`movers` の銘柄名・`theme_matrix.stocks`・`bought`/`sold` のセクター列）は囲まない（列側で配色されるため）。
  - **テーマ別資金フロー（`theme_matrix.rows`）**：1行＝1方向。`side`＝`"buy"`/`"sell"`、`theme`＝テーマ名、`stocks`＝主な銘柄（`・` 区切り・`[[…]]` で囲まない）、`background`＝背景説明（銘柄名は `[[…]]`）。**買い・売りが混在する1テーマは行を分ける**。別テーマ（例：個別の上方修正 と 原子力ルネサンス）は**別行**にする。
  - **箇条書き**：`thesis`・`overview.flow_conclusion` は論点ごとに配列要素へ分ける（各2〜3件目安）。
  - **重複回避**：`theme_matrix` と重複する内容を `bought`・`sold` の `themes`（下部「買われた/売られた主なテーマ」）に**再掲しない**（`themes` は原則 `[]`）。
  - **数値・略語**：`snapshot` の TOPIX 等は「終値（±%）」の順（例 `"4,064.6（+1.24%）"`）。**英略語は日本語で**（例 NFP→「非農業部門雇用者数」「米雇用統計」）。
  - **まとめの整合**：`flow_conclusion` で触れる個別イベントは `points`/`flow` 等で**事前に言及**しておく（未言及なら `flow_conclusion` で触れない）。

- **Claude が書く項目**：`thesis`（市況テーゼ。要点を配列で箇条書き可）／`strip`（注目セクター＝`sectors_up`/`sectors_down` にセクター名のみ。既定は `market_stats` の `strip_default`＝加重上位3/下位3。編集判断で差し替え可・CSV 実在名のみ）／`sector_flags`＋`sector_notes`（**`divergence_flags` の全件に応答義務**：⚠を採用するなら `sector_flags` にマーク＋`sector_notes` で「加重は大型株1銘柄の歪みで中央値・騰落数は別」を説明、採用しない場合は最終報告で理由に触れる）／`overview`（`snapshot` は決定的4行を `{"auto":"topix"|"breadth"|"top_sector"|"top_stock","note":"…"}` で置き、日経平均・為替など J-Quants 外の行のみ `label`/`value` を手書き＋出典明記。`points`/`flow`/`flow_conclusion`）／`bought`・`sold`（`table` に `{sector,note,flag?}`。`themes` は原則 `[]`）／`movers`（`gainers`/`losers` に `{code,note,links,emph?}`＋footnote）／`theme_matrix`（`{side,theme,stocks,background}` の行）／`news_sources`。
- **書かない項目（defaults/stats が供給）**：`title`・`universe`・`methodology`・`disclaimer`・`topix_pct`・`prev_date`・`generated_at`・最大代金セクター/銘柄の数値。
- **Stage2 の再利用**：値上がり側 movers がランキング rows（step3）と重複する銘柄は、step3 の `factor`/`factor_kind`・採用出典を**そのまま転用**し再リサーチしない。
- **値下がり側の追加リサーチ**：`movers_context`（TDnet）→ 株探 `https://kabutan.jp/stock/news?code=<4桁>`（ブラウザUA）→ Web検索「<コード> <銘柄名> 急落/ストップ安」の順。出典規律は step3 と同じ3層方針（`reference/sources.md §4`）・**ランディングページ出典禁止**・個人発信不使用を継承。
- **数値規律**：`note`/`themes` の数値は (a) CSV/stats に存在する値の言及、(b) 出典リンク付き記事からの引用、のみ。日経平均・為替など J-Quants 外の相場値は `snapshot` の手書き行に限り、①層出典（株探大引け・日経の東証大引け記事等）を `news_sources`「市場概況」に**必ず併記**したうえで記事内数値を転記する（創作禁止）。`links` は http(s) のみ（結合器が検証）。
- **出典規律（精密主張・2026-07-04 監査で恒久化）**：`scripts/validate_market_quality.py` が step3.5(d) で機械検査する。
  - **精密な数値・固有イベント**（時価総額順位・国内/世界シェア・目標株価・値上げ率・TOB/公開買付・非公開化・大量保有・上方/下方修正・格上げ/格下げ 等）は、一次（TDnet/EDINET/会社IR）→準一次→良質報道の順で裏取りし、**最初の言及の文末に `（[出典名](URL)）` を付ける**（SPA の `mdInline` が本文の Markdown リンクを描画する。実装変更不要）。`movers` の `note` は行の `links` にリンクがあれば文中リンクは不要。
  - **同一出典URLの重複掲載禁止（URL単位・2026-07-05 恒久化）**：同一URLの掲載は**本文中1箇所（インラインリンク・movers の `links` を含む）＋`news_sources` に1箇所の最大2箇所**まで。同一内容への2回目以降の言及には出典を再掲しない。同一内容を報じる**別ソースで URL が異なる場合は別カウント**（あくまで URL 単位のルール）。
  - `news_sources` は**補助一覧であり本文中リンクの代替にしない**。`news_sources[].links` の空は禁止。`emph: true` の movers は `links` 必須。
  - **ランディングページ出典禁止**（機械検査対象）：`minkabu.jp/stock/<code>`・`nikkei.com/nkd/company`・`finance.yahoo.co.jp/quote`・`kabutan.jp/stock/…`（銘柄トップ・finance・news 一覧）・`s.kabutan.jp/stocks/…`。具体記事（`kabutan.jp/news/…`・`s.kabutan.jp/news/n…`・`minkabu.jp/news/…`・`nikkei.com/article/…` 等）・TDnet/EDINET・会社 IR へ。TDnet の `release.tdnet.info` PDF は約1カ月で失効するため会社サイトの IR PDF を優先する。
  - **日次変動する順位は当日終値ベースで検証できない限り断定しない**（例：「時価総額国内トップ」は算出根拠が無ければ使わず、stats で検証済みの「売買代金全市場トップ」等で表現する）。
  - **品質ゲートに失敗しても本文を削って通さない**：第一手は Stage2 で収集済みの出典・`kabutan_news`・TDnet/EDINET の再利用によるリンク追加。削除・一般化は裏取り探索を尽くした後の最終手段とし、弱めた主張と理由を最終報告に列挙する（内容の充実度を落とさず正確性を担保するのが本規律の目的）。

## 関連

- 方法論：`skills/tse-ranking-digest/SKILL.md`
- 配信下敷き：`project-private/tdnet-monitor`（Pages＋Gmail）
- ルーチン方式の先行例：`pts-ranking-monitor`（PTS ナイト版・cron 06:06 JST）
