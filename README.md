# 東証 値上がり率ランキング・モニター

東証の日中（9:00–15:30）値上がり率ランキングを日次・無人で生成し、GitHub Pages と Gmail API で配信するリポジトリです。過去の公開データは `docs/data/` に保持し、作業中データは公開ルート外の `.work/<SESSION>/` に隔離します。

## 抽出条件

- 東証個別株のみ（J-Quants `ProdCat=011`、`Mkt∈{0111,0112,0113}`）
- 前日比 `+5%` 以上、売買代金 `1,000万円` 以上、時価総額 `100億円` 以上
- 値上がり率上位30社を掲載し、該当総数と掲載数を別々に記録

## 日次配信

1. runtime contractのhashを検証し、`scripts/wait_for_data.py` が未処理の最古営業日とbarsの鮮度を確認する。`SKIP` と `TIMEOUT` は無配信。
2. `scripts/build_day_ranking.py` がStage1を生成し、bars比率・masterカバー率・日付整合を検証する。
3. 決定的なcompact research planを最大5銘柄のバッチで調査し、検証済み `evidence.v1` と `factors.json` を生成する。`scripts/merge_factors.py` で反映し、ランキングJSONは手編集しない。
4. 同じevidenceを再利用したcompact market briefから市場分析をbest-effortで生成し、`scripts/publish.py` で公開成果物だけを `docs/` に生成する。この時点ではメールを送らない。
5. `docs/index.html` と `docs/data/` を `main` へpushする。
6. Pages上の当日artifact digestがローカル公開物と一致したことを確認してからGmailを送る。不一致・タイムアウト・送信失敗時は未送信のままエラーにする。

既存の `python scripts/*.py` CLIは互換入口であり、本体実装は `src/tse_ranking_monitor/` にあります。方法論は `vendor/tse-ranking-digest/`、共有データ取得コードは `market-scripts-common` のlock付きベンダリングを正本とします。

## 文書

- [作業規範](AGENTS.md)
- [日次ルーチン](runbook/DAILY_ROUTINE.md)
- [市場分析仕様](specs/MARKET_ANALYSIS.md)
- [内部パイプライン設計](specs/PIPELINE_ARCHITECTURE.md)
- [日次runtime contract](runbook/RUNTIME_CONTRACT.md)
- [セットアップ](runbook/SETUP.md)
- [ルーチン貼り付けプロンプト](runbook/ROUTINE_PROMPT.md)

起動は毎日16:35 JSTです。当日barsを待つ最終締切は18:10 JSTで、核ランキングの必須依存は当日barsです。

## 開発と検証

```bash
python scripts/check_vendor.py
python tools/check_methodology_vendor.py
python -m pytest
```

テストはネットワークを遮断して実行されます。`docs/data/` の日次更新だけではCIを起動しません。

## 免責

本情報は参考であり投資助言ではありません。投資判断は利用者自身が最新の一次情報を確認のうえ行ってください。
