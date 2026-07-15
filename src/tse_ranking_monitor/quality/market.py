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
  5. 精密主張リンク — 時価総額・目標株価・TOB 等のトリガー語は、同一 claim の最初の
                      言及に Markdown リンクを持つこと。movers は行ごとの links を要求し、
                      別銘柄の同じトリガー語で文書全体を免除しない
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
import tempfile
import unicodedata

from ..market import assemble as bmj

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

FINDINGS_SCHEMA_VERSION = "quality_findings.v1"
_ENTITY_RE = re.compile(r"\[\[([^\]]+)\]\]")


def finding(path, rule_id, severity, message, code=None):
    """Return one stable, JSON-serializable quality finding."""
    return {
        "code": str(code) if code not in (None, "") else None,
        "path": path or "$",
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
    }


def _human_finding(item):
    path = item.get("path")
    message = item.get("message") or ""
    return ("%s: %s" % (path, message)) if path and path != "$" else message


def select_repair_targets(findings, prefer_code=False):
    """Group findings into the smallest paths (or ranking codes) to repair."""
    grouped = {}
    for item in findings:
        code = item.get("code")
        key = ("code", code) if prefer_code and code else ("path", item.get("path") or "$")
        target = grouped.setdefault(key, {
            "code": code, "path": item.get("path") or "$",
            "rule_ids": [], "severities": [],
        })
        if item.get("rule_id") not in target["rule_ids"]:
            target["rule_ids"].append(item.get("rule_id"))
        if item.get("severity") not in target["severities"]:
            target["severities"].append(item.get("severity"))
    return list(grouped.values())


def write_json_atomic(path, payload):
    """Write a retry artifact atomically in its destination directory."""
    destination = os.path.abspath(path)
    parent = os.path.dirname(destination)
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".quality-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _claim_entities(text):
    """Entities used to distinguish a repeated claim from an unrelated one."""
    return set(_ENTITY_RE.findall(text or ""))


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


def audit_doc(doc):
    """Return machine-readable ERROR findings for one public market document."""
    try:
        bmj.validate_market(doc)
    except SystemExit:
        return [finding(
            "$", "MKT_STRUCTURE", "ERROR",
            "構造検証(validate_market)不合格 — stderr のスキーマエラーを先に直すこと（品質検査は未実施）")]

    findings = []
    ns = doc.get("news_sources") or []
    if not ns:
        findings.append(finding(
            "news_sources", "MKT_NEWS_SOURCES_REQUIRED", "ERROR",
            "news_sources が空。少なくとも「市場概況」の具体的な出典を載せる"))
    for i, source in enumerate(ns):
        if not (source.get("links") or []):
            findings.append(finding(
                "news_sources[%d](%s).links" % (i, source.get("topic")),
                "MKT_NEWS_SOURCE_LINK_REQUIRED", "ERROR",
                "links が空。トピックの根拠となる具体記事・開示 URL を1本以上載せる"))

    movers = doc.get("movers") or {}
    for side in ("gainers", "losers"):
        for i, mover in enumerate(movers.get(side) or []):
            if mover.get("emph") and not (mover.get("links") or []):
                findings.append(finding(
                    "movers.%s[%d](%s).links" % (side, i, mover.get("name") or mover.get("code")),
                    "MKT_EMPH_LINK_REQUIRED", "ERROR",
                    "emph=true（強調表示）だが links が空。Stage2 の採用出典を再利用してリンクを付ける",
                    mover.get("code")))

    def check_banned(url, path, code=None):
        for pattern, description in BANNED_URL_PATTERNS:
            if pattern.search(url or ""):
                findings.append(finding(
                    path, "MKT_BANNED_URL", "ERROR",
                    "ランディングページ出典は禁止（%s）: %s — 具体記事・TDnet/EDINET・会社IRへ差し替える"
                    % (description, url), code))

    for i, source in enumerate(ns):
        for j, link in enumerate(source.get("links") or []):
            check_banned(link.get("url"), "news_sources[%d](%s).links[%d]" % (
                i, source.get("topic"), j))
    for side in ("gainers", "losers"):
        for i, mover in enumerate(movers.get(side) or []):
            for j, link in enumerate(mover.get("links") or []):
                check_banned(
                    link.get("url"), "movers.%s[%d](%s).links[%d]" % (
                        side, i, mover.get("name") or mover.get("code"), j), mover.get("code"))

    units = list(iter_text_units(doc))
    for path, text, _links in units:
        for _label, url in MD_LINK_RE.findall(text):
            check_banned(url, path)

    # Precision coverage is claim-scoped, never trigger-scoped across the whole document.
    # A repeated narrative claim may reuse its first source when it names the same [[entity]].
    # Movers are stricter: each mover row must carry its own adjacent links, preventing an
    # unrelated stock's rating/TOB link from covering another mover with the same trigger.
    linked_claims = []
    for path, text, has_adjacent_links in units:
        norm = unicodedata.normalize("NFKC", text)
        triggers = [trigger for trigger in PRECISE_CLAIM_TRIGGERS if trigger in norm]
        if not triggers:
            continue
        linked = bool(MD_LINK_RE.search(text)) or has_adjacent_links
        entities = _claim_entities(text)
        is_mover = path.startswith("movers.gainers[") or path.startswith("movers.losers[")
        mover_name = re.search(r"^movers\.(?:gainers|losers)\[\d+\]\(([^)]+)\)", path)
        unit_code = None
        if mover_name:
            entities.add(mover_name.group(1))
        mover_location = re.search(r"^movers\.(gainers|losers)\[(\d+)\]", path)
        if mover_location:
            side, index = mover_location.group(1), int(mover_location.group(2))
            side_items = movers.get(side) or []
            if index < len(side_items) and isinstance(side_items[index], dict):
                unit_code = side_items[index].get("code")
        uncovered = []
        for trigger in triggers:
            same_claim_linked = linked or (
                not is_mover and bool(entities) and any(
                    previous_trigger == trigger and bool(entities & previous_entities)
                    for previous_trigger, previous_entities in linked_claims))
            if not same_claim_linked:
                uncovered.append(trigger)
        if uncovered:
            findings.append(finding(
                path, "MKT_PRECISE_CLAIM_SOURCE", "ERROR",
                "精密主張（%s）にリンク付きの言及が1箇所も無い（同一claim/moverスコープ）。"
                "最初の言及または当該 mover の links に出典を付ける"
                % "・".join(uncovered), unit_code))
        if linked:
            for trigger in triggers:
                linked_claims.append((trigger, entities))

    body_urls = {}
    for path, text, _links in units:
        for _label, url in MD_LINK_RE.findall(text):
            body_urls.setdefault(url, []).append(path)
    for side in ("gainers", "losers"):
        for i, mover in enumerate(movers.get(side) or []):
            for link in mover.get("links") or []:
                if link.get("url"):
                    body_urls.setdefault(link["url"], []).append(
                        "movers.%s[%d](%s).links" % (side, i, mover.get("name") or mover.get("code")))
    for url, paths in body_urls.items():
        if len(paths) > 1:
            findings.append(finding(
                paths[0], "MKT_DUPLICATE_BODY_URL", "ERROR",
                "同一URLが本文に%d回掲載（最初の言及1箇所のみに残す）: %s（%s）"
                % (len(paths), url, "、".join(paths))))

    news_urls = {}
    for i, source in enumerate(ns):
        for link in source.get("links") or []:
            if link.get("url"):
                news_urls.setdefault(link["url"], []).append(
                    "news_sources[%d](%s)" % (i, source.get("topic")))
    for url, paths in news_urls.items():
        if len(paths) > 1:
            findings.append(finding(
                paths[0], "MKT_DUPLICATE_NEWS_URL", "ERROR",
                "同一URLが news_sources に%d回掲載（1箇所に統合する）: %s（%s）"
                % (len(paths), url, "、".join(paths))))
    return findings


def check_doc(doc):
    """Backward-compatible ERROR strings; use :func:`audit_doc` for automation."""
    return [_human_finding(item) for item in audit_doc(doc)]


def audit_warnings(doc):
    """Return machine-readable non-blocking causal-attribution findings."""
    try:
        bmj.validate_market(doc)
    except SystemExit:
        return []
    findings = []
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
            findings.append(finding(
                path, "MKT_UNSOURCED_CAUSAL", "WARN",
                "因果表現（%s）に出典も推定マーカーも無い"
                "（出典を足す／推定表現にする／自データ寄与を確認して残す）"
                % "・".join(causal_hits)))
        if tier_a_hits:
            findings.append(finding(
                path, "MKT_UNSOURCED_DIRECT_MATERIAL", "WARN",
                "直接材料の帰属（%s）に出典も推定マーカーも無い"
                "（一次/準一次の出典を足す／推定表現〔連想・とみられる〕にする）"
                % "・".join(tier_a_hits)))
    return findings


def check_warnings(doc):
    """Backward-compatible WARN strings; use :func:`audit_warnings` for automation."""
    return [_human_finding(item) for item in audit_warnings(doc)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="市場分析 JSON の出典品質検証（構造検証 validate_market も内包）")
    ap.add_argument("paths", nargs="+", help="docs/data/<date>_market.json（複数可）")
    ap.add_argument("--format", choices=("human", "json"), default="human",
                    dest="output_format", help="出力形式（既定 human）")
    ap.add_argument("--repair-targets", nargs="?", const="-", default=None, metavar="PATH",
                    help="再生成すべき最小 path を JSON 出力（PATH省略時はstdout）")
    args = ap.parse_args(argv)

    all_errors = []
    file_results = []
    for p in args.paths:
        name = os.path.basename(p)
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            item = finding("$", "IO_READ_ERROR", "ERROR", "読み込み失敗: %s" % e)
            all_errors.append("%s: %s" % (name, _human_finding(item)))
            file_results.append({"file": p, "findings": [item]})
            continue
        errors = audit_doc(doc)
        warnings = audit_warnings(doc) if not errors or args.output_format == "json" or args.repair_targets else []
        file_results.append({"file": p, "findings": errors + warnings})
        if errors:
            all_errors.extend("%s: %s" % (name, _human_finding(item)) for item in errors)
        else:
            if args.output_format == "human" and not args.repair_targets:
                for item in warnings:
                    sys.stderr.write("[validate_market_quality] WARN: %s: %s\n" % (
                        name, _human_finding(item)))
                sys.stderr.write("[validate_market_quality] OK: %s（news_sources %d / movers %d / 本文 %d 要素%s）\n"
                                 % (name,
                                    len(doc.get("news_sources") or []),
                                    sum(len((doc.get("movers") or {}).get(s) or []) for s in ("gainers", "losers")),
                                    len(list(iter_text_units(doc))),
                                    "・WARN %d件" % len(warnings) if warnings else ""))

    if args.repair_targets:
        repair_payload = {
            "schema_version": FINDINGS_SCHEMA_VERSION,
            "validator": "market",
            "files": [{"file": result["file"],
                       "targets": select_repair_targets(result["findings"])}
                      for result in file_results],
        }
        if args.repair_targets == "-":
            json.dump(repair_payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            write_json_atomic(args.repair_targets, repair_payload)
    if args.output_format == "json":
        # When repair targets are sent to stdout, avoid concatenating two JSON docs.
        if args.repair_targets != "-":
            payload = {
                "schema_version": FINDINGS_SCHEMA_VERSION,
                "validator": "market",
                "files": file_results,
            }
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")

    if all_errors:
        if args.output_format == "human" and not args.repair_targets:
            sys.stderr.write("[validate_market_quality] ERROR: %d件\n%s\n修正方針: %s\n"
                             % (len(all_errors), "\n".join("  - " + e for e in all_errors), FIX_HINT))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
