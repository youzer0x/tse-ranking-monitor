#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市場分析 JSON の出典品質を検証する（構造検証 validate_market も内包）。

build_market_json.py の validate_market() が「SPA が描画できる形か」を守る番犬なのに
対し、本スクリプトは「書かれている内容が出典で裏付けられているか」を機械検査する：

  1. 構造検証       — build_market_json.validate_market()（不合格なら品質検査はスキップ）
  2. news_sources   — トピック一覧が空でなく、各トピックの links が空でないこと
  3. emph movers    — 強調表示（emph:true）の銘柄は links 必須
  4. 禁止 URL       — 銘柄ランディングページ（みんかぶ銘柄・株探銘柄・日経会社情報・
                      Yahoo!quote）を出典に使わない（具体記事・TDnet/EDINET・会社 IR へ）
  5. 精密主張リンク — 時価総額・目標株価・TOB 等のトリガー語は、そのトリガーに言及する
                      いずれかの本文要素（原則、最初の言及）に Markdown リンク
                      （[出典名](URL)）または movers の links があること
  6. 重複URL禁止    — 同一出典URLの掲載は本文中（インラインリンク＋movers.links）に
                      1箇所＋news_sources に1箇所の最大2箇所まで（URL単位。同一内容でも
                      URLが異なる別ソースは別カウント）
  7. 因果表現の監査 — 「点火」「波及」等の断定的因果語、および直接材料の帰属
                      （「決算/報道/開示/発表を受け」「材料視」）に出典・推定マーカーが
                      無ければ WARN（終了コードには影響しない。check_warnings）

検出時の修正方針（重要）：**主張を削って通さない**。Stage2 で収集済みの出典
（kabutan_news・TDnet・サブエージェント調査）を再利用して文末に `（[出典名](URL)）` を
足すのが第一手。削除・弱体化は裏取り探索を尽くした後の最終手段
（AGENTS.md §市場分析フラグメント執筆「出典規律」）。

標準ライブラリのみ。使い方:
  python scripts/validate_market_quality.py docs/data/2026-07-03_market.json [more.json ...]
"""
import argparse
import json
import os
import re
import sys
import unicodedata

import build_market_json as bmj

# 本文中の Markdown リンク。html_generator.py mdInline()（[text](url) → <a>）と同一パターン。
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

# 銘柄ランディングページ（禁止）。記事 URL は一致しないため許可される：
#   kabutan.jp/news/… / s.kabutan.jp/news/n… / minkabu.jp/news/… / finance.yahoo.co.jp/news/detail/…
BANNED_URL_PATTERNS = (
    (re.compile(r"minkabu\.jp/stock/"), "みんかぶ銘柄ページ（記事は minkabu.jp/news/…）"),
    (re.compile(r"nikkei\.com/nkd/company"), "日経会社情報ページ（記事は nikkei.com/article/…）"),
    (re.compile(r"finance\.yahoo\.co\.jp/quote"), "Yahoo!ファイナンス銘柄ページ（記事は /news/detail/… のみ可）"),
    (re.compile(r"kabutan\.jp/stock/"), "株探銘柄ページ（?code=・finance・news 一覧を含む。記事は kabutan.jp/news/…）"),
    (re.compile(r"s\.kabutan\.jp/stocks/"), "株探(sp)銘柄ページ（記事は s.kabutan.jp/news/n…）"),
)

# 精密主張トリガー語（監査指定10語＋自然対＋2026-07-05 監査で9語追加）。
# 判定は NFKC 正規化後（全角 ＴＯＢ 等も捕捉）。
# 見送り候補（誤検知が高いため不採用・再検討時の記録）:
#   裸の「シェア」= 自データの「代金シェア82.5%」等を誤爆／裸の「受注」=「受注回復期待」等の
#   推定表現を誤爆／「計画」「投資判断」「世界初」「国内初」= 文脈依存で保留
PRECISE_CLAIM_TRIGGERS = (
    "時価総額", "国内トップ", "世界シェア", "目標株価", "値上げ",
    "TOB", "大量保有", "上方修正", "下方修正", "格上げ",
    "格下げ", "公開買付", "非公開化",
    "世界首位", "国内唯一", "国内シェア", "受注残", "契約",
    "マイルストーン", "業務提携", "増産", "FY20",
)

# 因果表現の監査（WARN・終了コードに影響しない）：断定的な因果語を含む本文要素が
# 出典リンクも推定マーカーも持たない場合に警告する。対応は (a) 出典を足す
# (b) 推定表現にする (c) 自データ寄与（売買代金構成比・騰落数等）を確認して残す、の
# いずれか（AGENTS.md「要因帰属の規律」。残した WARN は最終報告に列挙する）。
# 「牽引」は自データ由来の「代金集中を牽引」等の誤検知が高いため v1 見送り（候補）。
# 将来課題: セクター寄与の自動定量チェック／theme_matrix×news_sources の対応検査。
CAUSAL_WORDS = ("点火", "波及", "誘発", "押し上げ", "主導", "直接受益")
HEDGE_MARKERS = ("とみられる", "連想", "思惑", "推定", "可能性", "期待", "一因", "並走", "示唆", "連れ高", "連れ安")
# 自データ寄与が同一要素内に明示されている場合（加重・中央値・売買代金等の定量文脈＝
# dominant-stock の歪み解説など）は、因果表現でも検証可能なので WARN を免除する。
OWN_DATA_MARKERS = ("中央値", "加重", "売買代金", "代金シェア", "億円", "⚠")

# 直接材料の帰属監査（WARN・因果語と同じ3免除）。AGENTS.md「要因帰属の規律」の文体階層(a)
# 「一次/準一次出典のある直接材料＝『〜を受けて』『材料視』」をコード化したもの。
# 直接材料の名詞（決算・報道・開示・発表）＋連用中止形「〜を受け(た/て)」に限定する：
#   ・「恩恵/影響/メリット/打撃を受け」はフォロワー（上昇に相乗りした＝相関）を表す正しい
#     語法であり、これを叩くと相関を相関として書く推奨形を誤検知するため対象にしない。
#   ・上方修正・下方修正・TOB・受注残・契約 等の精密イベントは精密主張トリガー（FAIL・
#     check_doc）が別途文末リンクを強制するため、ここでは重ねて監査しない。
# 将来課題: 直接材料名詞の拡充（増資・提携 等）は誤検知率を見て候補に留め置き。
TIER_A_RE = re.compile(r"(?:決算|報道|開示|発表)を受け|材料視")

FIX_HINT = ("主張を削らず出典を足して直す：Stage2 で収集済みの出典・kabutan_news・TDnet/EDINET・"
            "会社IRを同一要素の文末に `（[出典名](URL)）` で付ける。削除・弱体化は裏取り探索を"
            "尽くした後の最終手段（AGENTS.md §市場分析フラグメント執筆「出典規律」）")


def _strs(v):
    """str | [str] → [str]（それ以外の要素は構造検証済みなので黙って落とす）。"""
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def iter_text_units(doc):
    """検査対象の本文要素を (パス, テキスト, 隣接リンク有無) で列挙する。

    判定単位は「配列要素（文字列フィールド1本）」。文末の `（[出典](URL)）。` は
    「。」分割だとリンクだけが別文に落ちて誤検知するため、文分割はしない。
    対象は thesis / overview（points・flow・flow_conclusion・snapshot[].note）/
    movers（note・footnote）/ theme_matrix（rows・character）。
    title・universe・methodology・disclaimer・news_sources[].topic は定型・
    フィルタ条件の記述（「時価総額100億円以上」等）であり対象外。
    旧スキーマの sector_notes / bought・sold は 2026-07 改修で廃止済み
    （過去日の JSON に残っていても対象外として無視する）。
    """
    for i, t in enumerate(_strs(doc.get("thesis"))):
        yield ("thesis[%d]" % i, t, False)

    ov = doc.get("overview") or {}
    for k in ("points", "flow"):
        for i, t in enumerate(_strs(ov.get(k))):
            yield ("overview.%s[%d]" % (k, i), t, False)
    for i, t in enumerate(_strs(ov.get("flow_conclusion"))):
        yield ("overview.flow_conclusion[%d]" % i, t, False)
    for i, r in enumerate(ov.get("snapshot") or []):
        if isinstance(r, dict) and isinstance(r.get("note"), str):
            yield ("overview.snapshot[%d](%s).note" % (i, r.get("label")), r["note"], False)

    mv = doc.get("movers") or {}
    for side in ("gainers", "losers"):
        for i, m in enumerate(mv.get(side) or []):
            if isinstance(m, dict) and isinstance(m.get("note"), str):
                # 材料列に links が併記描画されるため、行の links 非空なら精密主張リンクを免除
                yield ("movers.%s[%d](%s).note" % (side, i, m.get("name") or m.get("code")),
                       m["note"], bool(m.get("links")))
        fn = mv.get("%s_footnote" % side)
        if isinstance(fn, str):
            yield ("movers.%s_footnote" % side, fn, False)

    tm = doc.get("theme_matrix") or {}
    if isinstance(tm, dict):
        for i, r in enumerate(tm.get("rows") or []):
            if isinstance(r, dict):
                # theme にトリガー語（例「電通総研TOB」）が入ってもヘッダセルへのリンク強制は
                # 不自然なので、theme＋background を1行ユニットに連結し background 側のリンクで満たせる
                joined = "。".join(x for x in (r.get("theme"), r.get("background")) if isinstance(x, str))
                if joined:
                    yield ("theme_matrix.rows[%d](%s)" % (i, r.get("theme")), joined, False)
        if isinstance(tm.get("character"), str):
            yield ("theme_matrix.character", tm["character"], False)


def check_doc(doc):
    """1ファイル分の品質検査。エラー文字列のリストを返す（空＝合格）。純粋関数。"""
    # 0) 構造検証。崩れていると本文 walker が信頼できないため、不合格なら品質検査はスキップ。
    try:
        bmj.validate_market(doc)
    except SystemExit:
        return ["構造検証(validate_market)不合格 — stderr のスキーマエラーを先に直すこと（品質検査は未実施）"]

    errors = []

    # 1) news_sources: 一覧が空・links 空を拒否
    ns = doc.get("news_sources") or []
    if not ns:
        errors.append("news_sources が空。少なくとも「市場概況」の出典（株探大引け・日経 東証大引け等）を載せる")
    for i, s in enumerate(ns):
        if not (s.get("links") or []):
            errors.append("news_sources[%d](%s): links が空。トピックの根拠となる具体記事・開示 URL を1本以上載せる"
                          % (i, s.get("topic")))

    # 2) emph:true の movers は links 必須
    mv = doc.get("movers") or {}
    for side in ("gainers", "losers"):
        for i, m in enumerate(mv.get(side) or []):
            if m.get("emph") and not (m.get("links") or []):
                errors.append("movers.%s[%d](%s): emph=true（強調表示）だが links が空。"
                              "Stage2 の採用出典・kabutan_news を再利用してリンクを付ける"
                              % (side, i, m.get("name") or m.get("code")))

    # 3) 禁止 URL（news_sources / movers の links ＋ 本文 Markdown リンクの3系統）
    def check_banned(url, ctx):
        for pat, desc in BANNED_URL_PATTERNS:
            if pat.search(url or ""):
                errors.append("%s: ランディングページ出典は禁止（%s）: %s — 具体記事・TDnet/EDINET・会社IRへ差し替える"
                              % (ctx, desc, url))

    for i, s in enumerate(ns):
        for j, lk in enumerate(s.get("links") or []):
            check_banned(lk.get("url"), "news_sources[%d](%s).links[%d]" % (i, s.get("topic"), j))
    for side in ("gainers", "losers"):
        for i, m in enumerate(mv.get(side) or []):
            for j, lk in enumerate(m.get("links") or []):
                check_banned(lk.get("url"),
                             "movers.%s[%d](%s).links[%d]" % (side, i, m.get("name") or m.get("code"), j))

    units = list(iter_text_units(doc))
    for path, text, _links in units:
        for _label, url in MD_LINK_RE.findall(text):
            check_banned(url, path)

    # 4) 精密主張トリガー：トリガー語ごとに「リンク付きの言及」が文書内に1箇所以上あること。
    #    同一URLの重複掲載を禁止している（下記5）ため、要素ごとのリンク要求はしない：
    #    最初の言及の文末にリンクを付け、以降の同一内容への言及には再掲しない（AGENTS.md「出典規律」）。
    mentions = {}   # トリガー語 -> 言及要素パスの一覧
    covered = set()  # リンク付き言及が1箇所以上あるトリガー語
    for path, text, has_adjacent_links in units:
        norm = unicodedata.normalize("NFKC", text)
        linked = bool(MD_LINK_RE.search(text)) or has_adjacent_links
        for t in PRECISE_CLAIM_TRIGGERS:
            if t in norm:
                mentions.setdefault(t, []).append(path)
                if linked:
                    covered.add(t)
    for t in sorted(mentions, key=lambda k: mentions[k][0]):
        if t not in covered:
            paths = mentions[t]
            errors.append("精密主張（%s）にリンク付きの言及が1箇所も無い（言及箇所: %s）。"
                          "最初の言及の文末に `（[出典名](URL)）` を付ける"
                          % (t, "、".join(paths[:4]) + ("…" if len(paths) > 4 else "")))

    # 5) 同一出典URLの重複掲載禁止（URL単位）：本文（インライン＋movers.links）1箇所＋
    #    news_sources 1箇所の最大2箇所まで。同一内容でも別URLなら別カウント。
    body_urls = {}
    for path, text, _links in units:
        for _label, url in MD_LINK_RE.findall(text):
            body_urls.setdefault(url, []).append(path)
    for side in ("gainers", "losers"):
        for i, m in enumerate(mv.get(side) or []):
            for lk in (m.get("links") or []):
                if lk.get("url"):
                    body_urls.setdefault(lk["url"], []).append(
                        "movers.%s[%d](%s).links" % (side, i, m.get("name") or m.get("code")))
    for url, paths in body_urls.items():
        if len(paths) > 1:
            errors.append("同一URLが本文に%d回掲載（最初の言及1箇所のみに残し他は削除する）: %s（%s）"
                          % (len(paths), url, "、".join(paths)))
    ns_urls = {}
    for i, s in enumerate(ns):
        for lk in (s.get("links") or []):
            if lk.get("url"):
                ns_urls.setdefault(lk["url"], []).append("news_sources[%d](%s)" % (i, s.get("topic")))
    for url, paths in ns_urls.items():
        if len(paths) > 1:
            errors.append("同一URLが news_sources に%d回掲載（1箇所に統合する）: %s（%s）"
                          % (len(paths), url, "、".join(paths)))

    return errors


def check_warnings(doc):
    """因果表現・直接材料帰属の監査（warning・終了コードに影響しない）。純粋関数。

    次のいずれかを含む本文要素が、出典リンク（インライン or movers の links）も推定マーカー
    （とみられる・連想 等）も自データ定量文脈（加重・売買代金 等）も持たない場合に警告する：
      ・因果語（点火・波及 等 CAUSAL_WORDS）
      ・直接材料の帰属（決算/報道/開示/発表を受け・材料視 = TIER_A_RE）
    どちらも「共起を出典なしで因果として書く」ことへの番犬。構造 NG のドキュメントは
    エラー側（check_doc）で報告済みなので対象外。
    """
    try:
        bmj.validate_market(doc)
    except SystemExit:
        return []
    warnings = []
    for path, text, has_adjacent_links in iter_text_units(doc):
        norm = unicodedata.normalize("NFKC", text)
        causal_hits = [w for w in CAUSAL_WORDS if w in norm]
        tier_a_hits = sorted({m.group(0) for m in TIER_A_RE.finditer(norm)})
        if not causal_hits and not tier_a_hits:
            continue
        # 免除条件は因果語・直接材料で共通（出典リンク／推定マーカー／自データ定量文脈）。
        exempt = (bool(MD_LINK_RE.search(text)) or has_adjacent_links
                  or any(m in norm for m in HEDGE_MARKERS)
                  or any(m in norm for m in OWN_DATA_MARKERS))
        if exempt:
            continue
        if causal_hits:
            warnings.append("%s: 因果表現（%s）に出典も推定マーカーも無い"
                            "（出典を足す／推定表現にする／自データ寄与を確認して残す）"
                            % (path, "・".join(causal_hits)))
        if tier_a_hits:
            warnings.append("%s: 直接材料の帰属（%s）に出典も推定マーカーも無い"
                            "（一次/準一次の出典を足す／推定表現〔連想・とみられる〕にする）"
                            % (path, "・".join(tier_a_hits)))
    return warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="市場分析 JSON の出典品質検証（構造検証 validate_market も内包）")
    ap.add_argument("paths", nargs="+", help="docs/data/<date>_market.json（複数可）")
    args = ap.parse_args(argv)

    all_errors = []
    for p in args.paths:
        name = os.path.basename(p)
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            all_errors.append("%s: 読み込み失敗: %s" % (name, e))
            continue
        errs = check_doc(doc)
        if errs:
            all_errors.extend("%s: %s" % (name, e) for e in errs)
        else:
            warns = check_warnings(doc)
            for w in warns:
                sys.stderr.write("[validate_market_quality] WARN: %s: %s\n" % (name, w))
            sys.stderr.write("[validate_market_quality] OK: %s（news_sources %d / movers %d / 本文 %d 要素%s）\n"
                             % (name,
                                len(doc.get("news_sources") or []),
                                sum(len((doc.get("movers") or {}).get(s) or []) for s in ("gainers", "losers")),
                                len(list(iter_text_units(doc))),
                                "・WARN %d件" % len(warns) if warns else ""))

    if all_errors:
        sys.stderr.write("[validate_market_quality] ERROR: %d件\n%s\n修正方針: %s\n"
                         % (len(all_errors), "\n".join("  - " + e for e in all_errors), FIX_HINT))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
