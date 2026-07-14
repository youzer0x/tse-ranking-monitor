# tse-ranking-monitor（自動化ルーチン仕様）

東証 日中（レギュラー）値上がり率ランキングを**日次・無人**で生成し、**GitHub Pages（Web）＋ Gmail 通知**で配信する Claude クラウドルーチンの仕様。本ディレクトリは独立リポ `tse-ranking-monitor` として切り出すための**雛形**である（PTS 版 `pts-ranking-monitor` と同じ分担）。

## 方法論の単一の真実源

- 方法論（抽出条件・時価総額算出・厳密窓・変動要因の裏取り・文体・品質ゲート）は
  **`news-financial-market/skills/tse-ranking-digest/SKILL.md`** が単一の真実源。本ファイルはそれに準拠する。
- データ取得系の共有スクリプト（`jquants.py`・`business_day.py`・`kabutan_pts.py`・`tdnet.py`・
  `market_cap_*.py`・`merge_factors.py`）とサブエージェント定義
  （`.claude/agents/stock-factor-researcher.md`）の**コード**は共有リポ **`market-scripts-common`** が
  単一の真実源（ベンダリング。各配布先の `vendor.lock.json` 参照・直接編集禁止）。
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
3. **Stage2（変動要因の充填）**：`rows`（上位50社）各銘柄の `factor`/`factor_kind` を
   **[開示]（TDnet 前営業日15:30以降∪当日15:30未満）→[報道]（一次記事＋配信時刻を当日セッションに整合）→[セクター連動クロスチェック]→[テーマ]** の順で埋める。
   検索要約を出典にせず、材料未確認は5パス確認後にのみ正直に記す（詳細は `SKILL.md §5 手順B`）。
   調査は **1銘柄=1サブエージェント（`stock-factor-researcher`・`.claude/agents/` 配布・編集禁止）の並列委譲**を基本とし、
   `ranking.json` への書き込みは**必ず `scripts/merge_factors.py` 経由**で行う（手編集しない）：
   - **(a) ハイブリッド判定（親が直接書く行）**：窓内 `disclosures[]` のタイトルだけで上昇が明快に説明できる行
     （決算・上方/下方修正・TOB・新株予約権・大型受注等）は親が直接 `factor`（具体的に）/`factor_kind="開示"` を起こす（委譲しない）。
   - **(b) 委譲**：残りの行を **1銘柄=1タスク・約10並列のバッチ**で `stock-factor-researcher` に委譲する。
     タスクプロンプト＝下の**【調査パラメータ】雛形**（`<SESSION>` 置換）
     ＋**当該 row の JSON 全体**（`disclosures`/`kabutan_news`/`sector_cluster` 含む）。返却は `{code, status, factor, factor_kind, sources}` の JSON 1個。
   - **(c) 収集とマージ**：親が直接書いた行のエントリと返却 JSON をあわせて **JSON 配列**として `docs/tmp/factors.json` に保存し、
     `python scripts/merge_factors.py --ranking docs/tmp/ranking.json --factors docs/tmp/factors.json` を実行する。
     `MISSING`/`REJECTED` の行は親がインライン調査（従来手順）で埋め、factors.json を更新して再実行する（factor が空の row を残さない）。
   - **(d) 横断整合（親の必須後工程）**：下記の**セクター連動クロスチェック**と**材料未確認ゲート**は横断視点（`theme_clusters`・全行）が
     必要なため**マージ後に親が実施**し、修正は factors.json 更新→merge_factors.py 再実行で反映する（`ranking.json` を手編集しない）。
   【調査パラメータ】雛形（委譲タスクプロンプトの先頭に貼る）：
   ```
   【調査パラメータ】
   - SESSION: <SESSION>（東証日中セッション＝9:00-15:30）
   - 材料窓: 前営業日15:30以降〜当日15:30未満（当日15:30ちょうど以降の開示は日中に反応不能＝要因にしない。今夜のPTS材料）。自社決算がこの窓外なら当日要因にせず、前営業日引け後の自社決算は「前日(YYYY-MM-DD)引け後」と開示日を明記する
   - [開示]の定義: row.disclosures（前営業日15:30以降∪当日15:30未満の TDnet 開示）が窓内材料
   - レーティング確認: disclosures が空なら row.kabutan_news（事前充填済み）と株探 https://kabutan.jp/stock/news?code=<4桁>（ブラウザUA）の「レーティング日報」「材料」を確認。寄り前（当日15:30より前）に伝わった格上げ・目標株価引き上げは有力材料（factor_kind=報道・証券会社名と旧→新の投資判断/目標株価を具体記載）
   - セクター文脈: row.sector_cluster（同一33業種の co-mover）を参考にする（クラスタ横断の最終帰属は親が行う）
   - 文体: である調。「開示なし」等の定型注記は書かない
   ```
   - **ソース規律（3層方針）**：①中核 whitelist は採用、②良質な非whitelist（フィスコ・みんかぶ編集記事等）はルーブリック合格なら採用、③個人発信・匿名・純アルゴ生成は不使用（`reference/sources.md §4`）。
   - **証券会社のレーティング変更（投資判断・目標株価）も必ずカバーする**。TDnet には出ないため、`disclosures` が空なのに日中上昇した銘柄は **株探の銘柄ニュース `https://kabutan.jp/stock/news?code=<4桁>`（ブラウザ UA）の「レーティング日報」「材料」**を確認する（`--kabutan-news` 実行時は各 row の `kabutan_news[]` に充填済み）。寄り前に出た格上げ・目標株価引き上げ（当日15:30より前に伝わったもの）は日中上昇の有力材料。証券会社名・旧→新の投資判断/目標株価を具体的に記し、区分は `[報道]`。
   - **セクター連動クロスチェック（必須）**：各 row の `sector_cluster`／トップレベル `theme_clusters` を読み、同一33業種で束で動いた銘柄は、クラスタ内で具体的[開示]を持つ銘柄（`has_disclosure=true`。`leader_code` は機械的ヒントなので開示内容を読んで真の牽引役を選び直す）を名指しで根拠化し `[テーマ]`（連鎖）として帰属する。業種をまたぐテーマ（例：光部品＝非鉄金属〔電線〕＋電気機器〔光部品〕＋精密機器）は各クラスタの leader と地合いから横断的に結ぶ。**同一テーマの co-mover を材料未確認で放置しない**。連鎖／継続（決算後ドリフト）／需給（前日反動・薄商い）の別は本文で書き分ける（区分は3タグ維持）。
   - **材料未確認ゲート**：「材料未確認」は (i)`disclosures` (ii)`kabutan_news`（株探材料/レーティング） (iii)Web 検索 `<コード> <銘柄名> 急騰/ストップ高` **＋事業内容から導いた業界キーワード（主要顧客・納入先・同業大手の増産/設備投資報道＝関連企業ニュースからの連想波及）** (iv)`sector_cluster` の leader 連動 (v)（M&A観・出来高急増の小型株は）EDINET の TOB/大量保有 を**すべて確認**してもなお当日窓に材料が無い行にのみ残す。**窓外の既知テーマを当日要因として書かない**（継続物色は起点報道の日付を明示。§市場分析フラグメント執筆「要因帰属の規律」参照）。**ペイウォールで本文が取れないだけで即材料未確認にしない**（他の①②媒体で二次裏取り→当日テーマへの帰属が成れば [テーマ]）。
   - **EDINET（任意・環境依存）**：EDINET DB MCP が利用可能な環境では、買収観・大量保有が疑われる行で TOB（公開買付届出）・大量保有報告書を一次情報として確認する。MCP が無い環境では本パスは任意で、既存パスへフォールスルーする（**ハードな依存にしない**）。
   - **品質検証（変動要因の機械ゲート）**：`factor`/`factor_kind` の充填完了後（merge_factors.py によるマージと (d) 横断整合の後・step3.5 の前）に `python scripts/validate_ranking_quality.py docs/tmp/ranking.json` を実行する。指摘の修正も factors.json 更新→merge_factors.py 再実行で反映する。市場分析タブと**同一基準**（因果語・精密主張トリガー・禁止URL・推定マーカーの語彙を `validate_market_quality.py` から import 再利用）で変動要因の出典品質を機械検査する（検査内容と対応は §「変動要因の品質規律」）。**非ゼロ終了（ERROR）でもランキング配信自体はブロックしない**（1行の弱い要因で当日全体を落とさない）。ERROR/WARN は**本文を削らず**裏取りで解消（第一手は `disclosures`・`kabutan_news`・TDnet/EDINET の再利用による再タグ・出典追加・推定表現化）→ step3 を最大2回やり直す。解消しきれない残件は最終報告に列挙する。
3.5. **市場分析タブのデータ生成（best-effort・ランキング配信をブロックしない）**：ランキングと**同一の push** に載せるため Publish（step4）の前に生成する。16:35 起動では当日 `/fins/summary`（速報~18:00頃）は未反映だが、**市場分析タブに表示されるのは bars/master/topix 由来の要素のみ**（セクター騰落・breadth・TOPIX・movers 表）で、当日決算開示は配信物に出ないため影響しない。手順の詳細は §「市場分析タブ（日次自動生成）」に従う。要点のみ：
   - **(a) 決定的データ**：`python scripts/build_market_stats.py --date <SESSION> --out-dir docs/tmp/market`。`docs/tmp/market/` に `sector_return_<SESSION>.csv`・`movers_top_<SESSION>.csv`（sector_analysis.py 移植版）と `market_stats_<SESSION>.json`（TOPIX 前日比・breadth・最大代金セクター/銘柄〔全ユニバース真値〕・セクター騰落率表「銘柄」列の主導銘柄 `sector_drivers`〔寄与順1〜2銘柄〕・**⚠乖離フラグ候補 `divergence_flags`**〔執筆ヒント〕・movers の TDnet 開示文脈 `movers_context`）を出力。
   - **(b) ナラティブ・フラグメント執筆**：`docs/tmp/market/narrative_<SESSION>.json`（**コミットしない**）を §「市場分析フラグメント執筆」の品質要件で執筆する。
   - **(c) 結合**：`python scripts/build_market_json.py --date <SESSION> --csv-dir docs/tmp/market --stats docs/tmp/market/market_stats_<SESSION>.json --defaults scripts/market_fragment_defaults.json --narrative docs/tmp/market/narrative_<SESSION>.json --out docs/data/<SESSION>_market.json`。バリデーション die はフラグメントを直して**最大2回**再実行。
   - **(d) 品質検証**：`python scripts/validate_market_quality.py docs/data/<SESSION>_market.json`。出典品質（news_sources/emph movers の links 空・ランディングページ URL・精密主張のリンク欠落・同一URLの重複掲載）を検査する。非ゼロ終了なら**出典を足して**（Stage2 の採用出典・`kabutan_news`・TDnet/EDINET の再利用が第一手。**本文の削除・弱体化で通すことを禁止**）フラグメントを直し、(b)〜(d) を最大2回再実行。裏取り探索を尽くしても出典が無い主張のみ数値を外して弱め、最終報告に理由を残す。**WARN（因果表現の監査）**はエラーではないが、各件について「出典追加／推定表現化／自データ寄与を確認して残す」のいずれかを行い、残した WARN は最終報告に列挙する。
   - **失敗時**：(a)〜(d) のどこで失敗しても市場分析は**スキップして step4 へ進む**（`docs/data/<SESSION>_market.json` が無くても SPA はタブ empty 表示に自然退避する。**ランキング配信は成功として扱う**）。`docs/tmp/` はコミットしない。
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

## 変動要因の品質規律（step3・機械ゲート `validate_ranking_quality.py`）

ランキングの `factor`/`factor_kind` は市場分析タブと**同一基準**でファクトチェックする。`validate_ranking_quality.py`
が step3 の充填後に機械検査し（因果語・精密主張トリガー・禁止URL・推定マーカーの語彙は `validate_market_quality.py`
から import 再利用＝唯一の真実源）、**内容の充実度を落とさず正確性を担保する**（削らず出典を足す・再タグ・推定表現化）。
市場分析の「要因帰属の規律」（§市場分析フラグメント執筆＝共起≠因果・文体の階層）を変動要因にも適用する。恒久ルール：

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
  日経会社情報/Yahoo!quote の銘柄ページを使わない（§市場分析フラグメント執筆「ランディングページ出典禁止」と同一）。
- **本文の出典リンク**：`factor` 内に `[出典名](URL)` を書けば Pages・メールとも自動でリンク描画される
  （`html_generator.py` の SPA `mdInline`／メール `_factor_html`。`開示` の窓内開示は従来どおり `[開示PDF]` を自動付与）。
  これで「削らず出典を足す」修復が変動要因でも成立する。

## 市場分析タブ（日次自動生成・**step3.5**）

- ランキング Pages（`docs/index.html`）は、値上がりランキングに加えて **「📊 市場分析」タブ**（`#market` ハッシュで開く）を持つ。データは**ランキング JSON とは別ファイル** `docs/data/<date>_market.json`（スキーマ v1）から fetch する（無い日付はタブ empty 表示に自然退避）。`manifest.json` には**載せない**（`update_manifest()` が `_market` を意図的に除外）。`cleanup_old()` は `<date>_market.json` も同じ30日保持で削除する（`base[:10]` 判定）。
- **二層構成（数値とナラティブの分離）**：数値は決定的スクリプトが、ナラティブは Claude が書き、結合器が機械ジョインする。転記ミスはセクター名・銘柄コードの完全一致バリデーションで構造的に排除される（不一致は非ゼロ終了）。
  1. **決定的データ** `scripts/build_market_stats.py`（`test-jquants` の `sector_analysis.py` を本リポへ移植した無人版。`jquants.py`/`business_day.py`/`tdnet.py` を再利用・stdlib＋`JQUANTS_API_KEY` のみ）。`--out-dir docs/tmp/market` に `sector_return_<date>.csv`・`movers_top_<date>.csv`（33業種加重/単純/中央値・値上/値下上位30）と `market_stats_<date>.json` を出力。stats には CSV 外の決定的数値（`topix_pct`／`prev_date`／`generated_at`／`breadth`／`top_sector_by_turnover`／`top_stock_by_turnover`〔全ユニバース真値〕／`sector_drivers`＝各33業種の騰落を最も主導した銘柄〔売買代金加重寄与 chg_pct×売買代金 の同方向上位。下落セクターは負の寄与順。第2位は寄与が首位の50%以上の場合のみ併記＝寄与順1〜2銘柄の配列。セクター騰落率表「銘柄」列に機械表示〕）と執筆ヒント（`divergence_flags`＝⚠乖離候補・`movers_context`＝movers の TDnet 開示）が入る。タブのセクター騰落率表の表示は**売買代金加重＋「銘柄」列のみ**（単純平均・中央値・騰落銘柄数は CSV に残り、執筆時の参照ヒントとして引き続き使える）。
  2. **ナラティブ・フラグメント**（Claude 執筆・`docs/tmp/market/narrative_<date>.json`・コミットしない）：§「市場分析フラグメント執筆」参照。
  3. **結合** `scripts/build_market_json.py`（stdlib のみ・冪等）：`--csv-dir docs/tmp/market --stats <market_stats>.json --defaults scripts/market_fragment_defaults.json --narrative <narrative>.json --out docs/data/<date>_market.json`。`--stats` から `topix_pct`/`prev_date`/`generated_at`/最大代金銘柄/`overview.snapshot` の auto 行を機械採用し、`sector_drivers` を `sectors33[].drivers`（セクター騰落率表「銘柄」列・寄与順1〜2銘柄）に機械 join、`breadth`・`n_liquid` を CSV と相互チェック（不一致は die）。`--defaults` は静的テンプレ（`title`/`universe`/`methodology`/`disclaimer`・`{n_liquid}` は結合時置換）を供給する。
  4. **品質検証** `scripts/validate_market_quality.py`（stdlib のみ・`validate_market` の構造検証も内包）：出典品質（links 空・禁止 URL・精密主張のリンク有無）を機械検査する。
- 生成は日次フロー **step3.5**（Publish の前）に組み込み済み。**best-effort**（失敗時はスキップして step4 へ・ランキング配信はブロックしない）。公開成果物は `docs/data/<date>_market.json` と再生成した `docs/index.html`。フラグメント・`docs/tmp/` はコミットしない。

## 市場分析フラグメント執筆

`docs/tmp/market/narrative_<date>.json` に**当日ナラティブのみ**を書く（数値・静的テンプレは書かない）。フラグメントはセクター名・銘柄コードのみを指定し、数値は CSV/stats からジョインされる。

- **フラグメントの JSON 形（厳守）**：結合器 `build_market_json.validate_market()` が SPA（`html_generator.py renderMarket`）の要求型を検証し、不一致は**die → 本節の形に直して再実行**（型崩れは配信画面のタブを空にする＝過去にフィールドを規定外の型にして renderMarket が例外→タブ空という事故があった）。次の**配列/オブジェクトの別を厳守**する：
  ```json
  {
    "thesis": ["…（要点を2〜3件の短文で。文字列1本でも可）"],
    "overview": {
      "snapshot": [{"auto": "topix|breadth|top_sector|top_stock"}, {"label": "…", "value": "…", "note": "…"}],
      "points": ["…"], "flow": ["…"], "flow_conclusion": ["…（2〜3件の短文。文字列1本でも可）"]
    },
    "movers": {"gainers": [{"code": "…", "note": "…", "links": [{"label": "…", "url": "https://…"}], "emph": true}],
               "gainers_footnote": "…",
               "losers":  [{"code": "…", "note": "…", "links": [{"label": "…", "url": "https://…"}]}],
               "losers_footnote": "…"},
    "theme_matrix": {"rows": [{"side": "buy|sell", "theme": "…", "stocks": "銘柄名・銘柄名", "background": "…"}], "character": "…"},
    "news_sources": [{"topic": "…", "links": [{"label": "…", "url": "https://…"}]}]
  }
  ```
  特に落とし穴：**`theme_matrix` はオブジェクト `{rows:[{side?,theme,stocks,background}], character}`（テーマ配列にしない・旧 `bought/sold` キーは廃止）／`overview.points`・`overview.flow` は文字列配列／`thesis`・`overview.flow_conclusion` は文字列 or 文字列配列（配列なら箇条書きで描画）**。`theme_matrix` が無ければ省略（結合器が `{}` にし SPA はテーマ節を出さない）。

- **編集レビュー由来の恒久規約（先回り適用・都度追記）**：市場分析タブはユーザーの編集レビューで書式・文体を確定してきた（2026-07-01/02＝内容・出典・数値の規律〔本節 各項〕、2026-07-03＝下記の表示・文体、2026-07-14＝市況サマリー帯・買われた/売られたセクター/テーマ・セクター注釈の廃止とセクター表の加重＋「銘柄」列化）。**日次自動生成はこれらを最初から適用**し、レビューで新たに確定した指摘は**その場限りにせず本節へ追記**して同じ指摘を繰り返させない（レビュー確定 → 本節へ恒久ルール化 → 次回以降 先回り適用、が運用規律）。表示・文体の具体規約：
  - **銘柄名の強調**：地の文（`thesis`／`overview.points`・`flow`・`flow_conclusion`／`movers.note`／`theme_matrix.background`）の**個別銘柄名は `[[銘柄名]]` で囲む**（SPA が配色し目立たせる。テーマ別資金フロー行では買い=赤/売り=緑に自動配色）。**表の専用カラム**（`movers` の銘柄名・`theme_matrix.stocks`）は囲まない（列側で配色されるため）。
  - **テーマ別資金フロー（`theme_matrix.rows`）**：1行＝1方向。`side`＝`"buy"`/`"sell"`、`theme`＝テーマ名、`stocks`＝主な銘柄（`・` 区切り・`[[…]]` で囲まない）、`background`＝背景説明（銘柄名は `[[…]]`）。**買い・売りが混在する1テーマは行を分ける**。別テーマ（例：個別の上方修正 と 原子力ルネサンス）は**別行**にする。
  - **`theme` はワンフレーズに集約する（2026-07-05 恒久化）**：「A→B」の因果連鎖表記（例「米雇用統計下振れ→FRB利上げ観測後退」）は視認性が悪いので避け、**因果・波及の説明は `background` 側に書く**（例：theme「米雇用統計下振れ」＋背景で「→FRB利上げ観測後退→…」）。どうしても `theme` に「→」が必要な場合のみ使用可（SPA が「→」の直前で改行し2行表示する）。
  - **箇条書き**：`thesis`・`overview.flow_conclusion` は論点ごとに配列要素へ分ける（各2〜3件目安）。
  - **数値・略語**：`snapshot` の TOPIX 等は「終値（±%）」の順（例 `"4,064.6（+1.24%）"`）。**英略語は日本語で**（例 NFP→「非農業部門雇用者数」「米雇用統計」）。
  - **まとめの整合**：`flow_conclusion` で触れる個別イベントは `points`/`flow` 等で**事前に言及**しておく（未言及なら `flow_conclusion` で触れない）。

- **Claude が書く項目**：`thesis`（市況テーゼ＝市場分析タブ冒頭に表示。要点を配列で箇条書き可）／`overview`（`snapshot` は決定的4行を `{"auto":"topix"|"breadth"|"top_sector"|"top_stock","note":"…"}` で置き、日経平均・為替など J-Quants 外の行のみ `label`/`value` を手書き＋出典明記。`points`/`flow`/`flow_conclusion`）／`movers`（`gainers`/`losers` に `{code,note,links,emph?}`＋footnote）／`theme_matrix`（`{side,theme,stocks,background}` の行）／`news_sources`。`market_stats` の `divergence_flags`（⚠乖離候補）は**執筆ヒント**＝`thesis`/`overview` で言及するかの判断材料（セクター騰落率表の「銘柄」列が主導銘柄を機械表示するため注記義務なし）。
- **書かない項目（defaults/stats が供給）**：`title`・`universe`・`methodology`・`disclaimer`・`topix_pct`・`prev_date`・`generated_at`・最大代金セクター/銘柄の数値・セクター騰落率表の「銘柄」列（stats の `sector_drivers` を結合器が `sectors33[].drivers` に機械 join）。
- **Stage2 の再利用**：値上がり側 movers がランキング rows（step3）と重複する銘柄は、step3 の `factor`/`factor_kind`・採用出典を**そのまま転用**し再リサーチしない。
- **値下がり側の追加リサーチ**：`movers_context`（TDnet）→ 株探 `https://kabutan.jp/stock/news?code=<4桁>`（ブラウザUA）→ Web検索「<コード> <銘柄名> 急落/ストップ安」の順。出典規律は step3 と同じ3層方針（`reference/sources.md §4`）・**ランディングページ出典禁止**・個人発信不使用を継承。
- **数値規律**：`note`/`background` 等の地の文の数値は (a) CSV/stats に存在する値の言及、(b) 出典リンク付き記事からの引用、のみ。日経平均・為替など J-Quants 外の相場値は `snapshot` の手書き行に限り、①層出典（株探大引け・日経の東証大引け記事等）を `news_sources`「市場概況」に**必ず併記**したうえで記事内数値を転記する（創作禁止）。`links` は http(s) のみ（結合器が検証）。
- **出典規律（精密主張・2026-07-04 監査で恒久化）**：`scripts/validate_market_quality.py` が step3.5(d) で機械検査する。
  - **精密な数値・固有イベント**（時価総額順位・国内/世界シェア・目標株価・値上げ率・TOB/公開買付・非公開化・大量保有・上方/下方修正・格上げ/格下げ 等）は、一次（TDnet/EDINET/会社IR）→準一次→良質報道の順で裏取りし、**最初の言及の文末に `（[出典名](URL)）` を付ける**（SPA の `mdInline` が本文の Markdown リンクを描画する。実装変更不要）。`movers` の `note` は行の `links` にリンクがあれば文中リンクは不要。
  - **同一出典URLの重複掲載禁止（URL単位・2026-07-05 恒久化）**：同一URLの掲載は**本文中1箇所（インラインリンク・movers の `links` を含む）＋`news_sources` に1箇所の最大2箇所**まで。同一内容への2回目以降の言及には出典を再掲しない。同一内容を報じる**別ソースで URL が異なる場合は別カウント**（あくまで URL 単位のルール）。
  - **一次ソース優先（2026-07-05 恒久化）**：出典リンクは一次情報を優先する — 会社開示・EDINET・**当該事実を最初に報じた媒体の記事**。二次配信記事（トレーダーズ・ウェブ／フィスコ／Yahoo!転載等）が「＝日経」のように**一次媒体を明示している場合は、一次媒体の記事URLを探して出典にする**。二次記事の使用は (a) 一次記事が特定できない、(b) 市場反応（買い気配・急伸等）自体を根拠にする場合の補助に限る。同一記事の転載ミラー（株探記事の Yahoo!転載等）は同一記事扱いで可。**本文に書く媒体名は実際にリンクする媒体と一致させる**（例：「日経報道（[トレーダーズ・ウェブ](…)）」は不可）。
  - `news_sources` は**補助一覧であり本文中リンクの代替にしない**。`news_sources[].links` の空は禁止。`emph: true` の movers は `links` 必須。
  - **ランディングページ出典禁止**（機械検査対象）：`minkabu.jp/stock/<code>`・`nikkei.com/nkd/company`・`finance.yahoo.co.jp/quote`・`kabutan.jp/stock/…`（銘柄トップ・finance・news 一覧）・`s.kabutan.jp/stocks/…`。具体記事（`kabutan.jp/news/…`・`s.kabutan.jp/news/n…`・`minkabu.jp/news/…`・`nikkei.com/article/…` 等）・TDnet/EDINET・会社 IR へ。TDnet の `release.tdnet.info` PDF は約1カ月で失効するため会社サイトの IR PDF を優先する。
  - **日次変動する順位は当日終値ベースで検証できない限り断定しない**（例：「時価総額国内トップ」は算出根拠が無ければ使わず、stats で検証済みの「売買代金全市場トップ」等で表現する）。
  - **品質ゲートに失敗しても本文を削って通さない**：第一手は Stage2 で収集済みの出典・`kabutan_news`・TDnet/EDINET の再利用によるリンク追加。削除・一般化は裏取り探索を尽くした後の最終手段とし、弱めた主張と理由を最終報告に列挙する（内容の充実度を落とさず正確性を担保するのが本規律の目的）。

- **要因帰属の規律（2026-07-05 恒久化・岡野バルブ誤帰属の再発防止）**：急騰・急落の「なぜ動いたか」は次の優先順で確認して帰属する。
  - **(i) 当日窓（前営業日15:30〜当日15:30）の自社開示** → **(ii) 窓内の関連企業・業界ニュースからの連想波及** → (iii) セクター連動クロスチェック → (iv) 継続テーマ。(ii) では Web検索「<銘柄名> 急騰 <日付>」に加え、**事業内容から導いた業界キーワード**（主要顧客・納入先・同業大手の増産/設備投資/大型受注の報道。例：発電用バルブ→ガスタービン増産・原発再稼働・電力インフラ投資）で当日報道を必ず探す。
  - **窓外の既知テーマ（過去の報道）を当日ドライバーとして書かない**。窓内に新規材料が無い場合のみ「継続物色」「テーマ再燃」と明示し、**テーマ起点の報道日付を本文に記す**（例「6/28の三菱重工ガスタービン増産報道以来の」）。日付の無い曖昧な「〜報道を受け」は禁止。
  - **事実と推論を書き分ける**：直接受注・業績寄与が未確認の関連は「連想」「思惑」と明示して断定しない。急騰を単一要因に断定せず、トリガー報道×業績下地×需給（小型株の値動きの軽さ）の重なりとして書く。掲示板・SNS は市場心理の把握目的でも引用・参照しない（既存規律のまま）。
  - **セクター因果の限定（2026-07-05 追記・OSG「点火剤」過大表現の再発防止）**：「点火剤」「セクター全体へ波及」等のセクター因果は、報道等で裏付けられる場合か、当該材料に帰属できる銘柄の範囲に限定して使う。裏付けの無い同業連動は「連れ高（とみられる）」等の**推定表現**にとどめる。ある銘柄を「セクター上昇の主因」と書く前に**自データで主導性を確認**する（売買代金構成比・加重寄与が小さい銘柄をセクター全体の主因としない）。複数テーマ・地合いが併存する広範高（騰落数で確認）は「並走」と書く。`sector_cluster` の **`leader_code` は機械的ヒントであり因果の証拠ではない**（開示内容と自データ寄与で判断する）。
  - **33業種クラスタの異質性に注意（2026-07-10 恒久化・ローツェ〔6323〕の機械セクター過大帰属の再発防止）**：`sector_cluster`（sec33 単位）の co-mover を「同一テーマの連れ高」の根拠として**名指す前に**、その33業種が異質な事業を含みうることを確認する（例：**「機械」＝工作機械〔DMG森精機・ツガミ等〕＋半導体製造装置〔ローツェ・サムコ等〕が混在**。半導体テーマの連れ高を語るのに工作機械銘柄を証拠として挙げるのは共起≠因果の過大帰属）。真に同一ドライバー・同一テーマの銘柄だけを名指し、**別テーマで動いた同業種銘柄をテーマ連れ高の証拠にしない**。裏付けの取れる同テーマ銘柄を挙げられない場合は、個別銘柄名を出さずセクター/テーマ水準の推定表現（「〜関連株全般への買い戻しに連れ高したとみられる」）にとどめる。
  - **共起と因果を区別する（2026-07-05 恒久化）**：ある材料 A とセクター/銘柄 X の上昇が同時に起きても、それだけでは「A が X を上げた」とは言えない（A は X の上昇に相乗りしただけ＝相関の可能性がある）。因果として断定する前に **(1) 時系列**（材料が値動きに先行したか。当日15:30以降の開示や反応不能な後追い報道を要因にしない）、**(2) 定量寄与**（A の売買代金構成比・加重寄与が X を主導する規模か）、**(3) 代替要因**（地合い・為替・米国市場・別テーマが真因でないか。`market_stats` の TOPIX 前日比・breadth〔騰落数〕・`divergence_flags` で確認）の3点を確認する。1つでも欠ける、または真因が別にありそうなら「連れ高（とみられる）」「並走」等の推定表現にとどめる。
  - **自社決算の開示時刻を要因判定に厳密反映する（2026-07-10 恒久化・ローツェ〔6323〕誤帰属の再発防止）**：銘柄自身の決算・業績修正が**当日15:30ちょうど以降**（後場終了後）に開示された場合、当日の日中値動きには反応不能＝**今夜のPTS／翌営業日の材料**であり、当日ランキングの上昇要因にしてはならない（`disclosures[]` は前営業日15:30以降∪当日15:30未満の窓で機械充填されるため、当日15:30ちょうどの自社決算は窓外＝載らない）。**前営業日15:30以降**に出た自社決算は当日の有効な材料だが、本文では必ず『**前営業日（YYYY-MM-DD 15:30）引け後の決算**』と開示日を明記し、「同日に決算発表」のように当日開示と混同しない。例：ローツェ（6323）の1Q決算は 2026-07-09 15:30 開示＝**7/9 の日中上昇要因にはできず**、**7/10 の要因としてなら「前日（7/9）引け後の好決算」**と日付を明記して書く。
  - **自社の確定材料に「連想（買い）」を使わない（2026-07-10 恒久化）**：「連想」「連想買い」は、他社材料・業界ニュース・テーマからの波及で、当該銘柄への**直接の受注・業績寄与が未確認**のときに断定を避けるヘッジ語である（本節「事実と推論を書き分ける」「下記 文体の階層(b)」）。当該銘柄**自身の決算・受注・業績**が上昇要因の行では、それは直接の**好感買い／業績評価**であり、「**好決算を好感した買い**」「業績を評価した買い」等の直接表現を用いる（自社の確定材料を「連想」で書くと因果を弱め誤読させる）。
  - **文体の階層（2026-07-05 恒久化）**：確度に応じて表現を使い分ける — (a) 一次/準一次出典のある直接材料＝「〜を受けて」「〜を材料視」、(b) 同業・テーマからの推論＝「連想」「〜とみられる」「連れ高か」、(c) 定量寄与が弱い・複数要因併存＝「一因」「並走」。因果語（点火・波及・誘発・押し上げ・主導・直接受益）や直接材料の帰属（「決算/報道/開示/発表を受け」「材料視」）を出典・推定マーカー無しで使うと `validate_market_quality.py` が **WARN** を出す。
  - **根拠パック（執筆前チェック・2026-07-05 恒久化）**：フラグメント執筆前に `docs/tmp/ranking.json` の `theme_clusters`／各 row の `factor`・`disclosures` 有無と、`market_stats` の寄与情報（最大代金・divergence_flags）を確認し、**その範囲で書く**（根拠に無いセクター因果・テーマ帰属を創作しない。一覧化スクリプトは将来課題）。

## 関連

- 方法論：`skills/tse-ranking-digest/SKILL.md`
- 配信下敷き：`project-private/tdnet-monitor`（Pages＋Gmail）
- ルーチン方式の先行例：`pts-ranking-monitor`（PTS ナイト版・cron 06:06 JST）
