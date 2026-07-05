"""pytest 共通設定：scripts/ を import パスに載せ、凍結ゴールデンを供給する。

scripts/ はパッケージ（__init__.py 付き）ではなくフラットなスクリプト置き場なので、
`import business_day` 等が通るよう scripts/ を sys.path 先頭に挿す。conftest.py は
テストモジュールより先に読まれるため、ここで一度だけ配線すれば全テストで有効になる。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def market_golden():
    """docs/data の実出力を凍結した market.json（validate_market の正常系サンプル）。

    docs/data/ は publish.py が約30日で削除するため、テストは docs/data/ を直接読まず、
    tests/fixtures/ の日付付きスナップショットを固定で使う。市況の数値が古びても、
    「この入力に対してロジックが同じ答えを出す」ことを検証する用途では不変でよい。
    """
    with open(FIXTURES / "market_2026-07-03.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def ranking_golden():
    """validate_ranking_quality の正常系サンプル（全チェックを通す最小ランキング JSON）。

    market_golden と違い実データのスナップショットではなく**手書きの合成 doc**。実 docs/data/ の
    ランキングは監査で誤りが混じっている（開示タグの窓外材料・無出典因果 等）ため golden に使えない。
    開示（窓内 disclosure・日付一致）／テーマ（連想・推定表現）／報道（kabutan_news 裏付け）／
    材料未確認フォールバックの4行で、check_ranking / check_ranking_warnings をともに空で通す。
    """
    with open(FIXTURES / "ranking_sample.json", encoding="utf-8") as f:
        return json.load(f)
