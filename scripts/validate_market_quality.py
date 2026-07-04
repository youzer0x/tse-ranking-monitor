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
  5. 精密主張リンク — 時価総額・目標株価・TOB 等のトリガー語を含む本文要素は、
                      同一要素内に Markdown リンク（[出典名](URL)）を要求
                      （movers の note は行の links 非空でも可＝材料列にリンクが併記されるため）

検出時の修正方針（重要）：**主張を削って通さない**。Stage2 で収集済みの出典
（kabutan_news・TDnet・grok research）を再利用して文末に `（[出典名](URL)）` を
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

# 精密主張トリガー語（監査指定10語＋自然対）。含む本文要素は同一要素内に出典リンク必須。
# 判定は NFKC 正規化後（全角 ＴＯＢ 等も捕捉）。
# 拡張候補（編集レビューのループで採用を判断）: 国内シェア・国内唯一・マイルストーン・投資判断・世界初・国内初
PRECISE_CLAIM_TRIGGERS = (
    "時価総額", "国内トップ", "世界シェア", "目標株価", "値上げ",
    "TOB", "大量保有", "上方修正", "下方修正", "格上げ",
    "格下げ", "公開買付", "非公開化",
)

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
    title・universe・methodology・disclaimer・news_sources[].topic は定型・
    フィルタ条件の記述（「時価総額100億円以上」等）であり対象外。
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

    for i, n in enumerate(doc.get("sector_notes") or []):
        if isinstance(n, dict) and isinstance(n.get("text"), str):
            yield ("sector_notes[%d].text" % i, n["text"], False)

    for side in ("bought", "sold"):
        sd = doc.get(side) or {}
        for i, r in enumerate(sd.get("table") or []):
            if isinstance(r, dict) and isinstance(r.get("note"), str):
                yield ("%s.table[%d](%s).note" % (side, i, r.get("sector")), r["note"], False)
        for i, t in enumerate(sd.get("themes") or []):
            if not isinstance(t, dict):
                continue
            if isinstance(t.get("title"), str):
                yield ("%s.themes[%d].title" % (side, i), t["title"], False)
            for j, b in enumerate(t.get("bullets") or []):
                if isinstance(b, str):
                    yield ("%s.themes[%d].bullets[%d]" % (side, i, j), b, False)

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

    # 4) 精密主張トリガー：同一要素内にリンクが無ければエラー
    for path, text, has_adjacent_links in units:
        norm = unicodedata.normalize("NFKC", text)
        hits = [t for t in PRECISE_CLAIM_TRIGGERS if t in norm]
        if hits and not MD_LINK_RE.search(text) and not has_adjacent_links:
            errors.append("%s: 精密主張（%s）に出典リンクが無い" % (path, "・".join(hits)))

    return errors


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
            sys.stderr.write("[validate_market_quality] OK: %s（news_sources %d / movers %d / 本文 %d 要素）\n"
                             % (name,
                                len(doc.get("news_sources") or []),
                                sum(len((doc.get("movers") or {}).get(s) or []) for s in ("gainers", "losers")),
                                len(list(iter_text_units(doc)))))

    if all_errors:
        sys.stderr.write("[validate_market_quality] ERROR: %d件\n%s\n修正方針: %s\n"
                         % (len(all_errors), "\n".join("  - " + e for e in all_errors), FIX_HINT))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
