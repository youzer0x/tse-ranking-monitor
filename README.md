# 東証 値上がり率ランキング・モニター

東証の**日中（レギュラー）取引**（前場 9:00–11:30／後場 12:30–15:30）の株価上昇率ランキング（値上がり専用）を**日次・無人**で生成し、**GitHub Pages（Web）＋ Gmail（API）**で配信する Claude のスケジュール（ルーチン）です。PTS ナイト版 `pts-ranking-monitor` と同じ構成・同じ Gmail 方式。

## 抽出条件
- 東証個別株のみ（J-Quants `ProdCat=011`＋`Mkt∈{0111,0112,0113}`。ETF/REIT・地方単独上場は除外）
- 値上がり率 **≥ 前日比 +5%** かつ 売買代金 **≥ ¥10,000,000**
- 時価総額 **≥ 100億円**（当日終値 × 発行済株式数・億円四捨五入）
- **掲載上限＝値上がり率上位50社**（該当が50社超なら上位50社のみ掲載）

## 仕組み
1. `scripts/wait_for_data.py` … 当日が東証営業日かを判定（休場ならスキップ）し、当日四本値が J-Quants に反映されるまで待つ鮮度ガード（未反映のまま締切なら配信しない）。※`scripts/check_gate.py` は純・ネット無しの営業日判定として手動確認/フォールバック用に残置
2. `scripts/build_day_ranking.py` … J-Quants V2 で決定的にスクリーニング（Stage1）。TDnet 開示（前営業日15:30以降∪当日15:30未満）と株探†を結合
3. Claude が各銘柄の変動要因（[開示]→[報道]→[テーマ]）を一次情報で裏取り（Stage2）
4. `scripts/publish.py` … `docs/`（Pages JSON/SPA）更新＋ `scripts/gmail_sender.py` で Gmail API 送信
5. `docs/` を **main** に push（Pages 公開）

## セットアップ
**[setup/SETUP.md](setup/SETUP.md)** に Step 0〜9 を詳述。ルーチンの貼り付け文面は **[setup/ROUTINE_PROMPT.md](setup/ROUTINE_PROMPT.md)**、ルーチン仕様は **[AGENTS.md](AGENTS.md)**。
起動は **毎日 16:35 JST**（`scripts/wait_for_data.py` が当日四本値〔J-Quants 公式反映「16:30頃」〕の確定をポーリングで待つ鮮度ガード付き。通常は16:30台に確定→即実行、遅延日のみ待機し、打ち切りは 18:10 JST 壁時計で「現状より遅くしない」を保証）。核ランキングの必須依存は四本値のみで、マスタ17:30・財務速報18:00 は使わない。

## 免責
本情報は参考であり投資助言ではない。投資判断は利用者自身が最新の一次情報を確認のうえ行うこと。
