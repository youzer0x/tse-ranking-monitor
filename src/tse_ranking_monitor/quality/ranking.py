#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""値上がり率ランキング JSON の変動要因（factor）の出典品質を機械検査する。

市場分析タブの番犬 `validate_market_quality.py`（vmq）と**同一の基準**をランキングの
`rows[].factor`/`factor_kind` に適用する姉妹スクリプト。因果語・精密主張トリガー・禁止URL・
推定マーカー等の**語彙は vmq から import 再利用**し（唯一の真実源）、ランキング固有の構造
（factor_kind＝開示/報道/テーマ・行ごとの窓内 TDnet 開示 disclosures[]）に合わせた検査を足す。

契約は vmq と同じ二層：
  check_ranking(doc)          -> エラー文字列のリスト（非空なら exit 1）。純粋関数。
  check_ranking_warnings(doc) -> 警告文字列のリスト（stderr・exit code に影響しない）。純粋関数。

検査（重大度）：
  1. [ERROR] factor_kind=開示 なのに disclosures[] が空。
     build_day_ranking.py は「前営業日15:30以降∪当日15:30未満」の**窓内 TDnet 開示のみ**を
     disclosures[] に入れる（tdnet.disclosures_window）。よって窓内開示が無いのに「開示」タグは
     裏付けの無いラベル。**EDINET の大量保有/TOB・証券会社レーティングは TDnet に出ない＝
     [報道]（一次URLを本文に明記）で書く。窓外・継続材料は [テーマ]（起点報道日を明記）。**
  2. [WARN]  factor_kind=開示 で本文に日付を挙げるが、その日付が disclosures[].date・session・prev の
     どれとも一致しない（材料日付のドリフト疑い）。複数日付のうち1つでも一致すれば不問。
  3. [WARN]  factor に断定的因果語（vmq.CAUSAL_WORDS）・直接材料の帰属（vmq.TIER_A_RE）・外部報道の
     断定（「〜との報道」等 _REPORT_ATTR_RE＝岡野バルブ誤帰属型）があるのに、一次開示（開示タグ＋
     disclosures）・インラインリンク・kabutan_news・推定マーカー・自データ定量文脈のいずれの裏付けも無い。
     → 連想・連れ高・並走 等の推定表現にするか、起点報道日と記事URLを本文に足す。
  3c.[WARN]  factor が自社決算を上昇要因に挙げる（決算系語×帰属語の共起）のに、窓内 disclosures[] に
     決算開示が無い＝決算が窓外（当日15:30以降の今夜PTS材料／前営業日より前の旧材料）で当日の日中
     要因にできない（ローツェ〔6323〕型）。自社決算の行に「連想（買い）」があるのも矛盾として WARN。
  4. [WARN]  factor_kind=報道 で企業アクション語（目標株価・TOB・上方修正・格上げ 等）を挙げるが、
     disclosures・kabutan_news・インラインリンク・推定マーカーのいずれの裏付けも無い。
  5. [WARN]  factor_kind が空／{開示,報道,テーマ} 以外／factor が空。
  6. [ERROR] disclosures[].pdf_url または factor 内 Markdown リンクが禁止ランディングページURL。

既知の軽微な誤検知（許容・WARN のため）：check2 は「2026-06-18/19開示」等の**圧縮日付レンジ**の
後半日（19）を拾えず前半日（18）だけで照合するため、稀に不一致 WARN を出しうる。出典を足すか
日付を disclosures 準拠に直せば消える。

標準ライブラリ＋vmq のみ・ネット/APIキー/実行日時に非依存（日付は doc の session_date/prev_date から取る）。
使い方:
  python scripts/validate_ranking_quality.py docs/data/2026-07-03.json [more.json ...]
"""
import argparse
import collections
import json
import os
import re
import sys
import unicodedata

from . import market as vmq

VALID_KINDS = ("開示", "報道", "テーマ")

# check4 の対象＝「企業アクション」語に絞る。vmq.PRECISE_CLAIM_TRIGGERS には契約・FY20・増産・
# マイルストーン・時価総額 等の正当な factor 本文に頻出する語も含まれ、そのまま使うと誤爆する。
# リテラル複製でなく vmq との積集合で定義し、vmq が語を落とせばランキングも自動追随する（唯一の真実源）。
_CORP_ACTION = ("目標株価", "TOB", "大量保有", "上方修正", "下方修正",
                "格上げ", "格下げ", "公開買付", "非公開化")
RANKING_CLAIM_TRIGGERS = tuple(t for t in vmq.PRECISE_CLAIM_TRIGGERS if t in _CORP_ACTION)

# 外部報道/観測を当日ドライバーとして断定する構文（「〜との報道」「検討が浮上」等）。断定的因果語
# （vmq.CAUSAL_WORDS）にも「〜を受け」（vmq.TIER_A_RE）にも該当しないが、出典も推定マーカーも無ければ
# 「無出典の具体報道」＝岡野バルブ誤帰属型（AGENTS.md 要因帰属の規律「日付の無い『〜報道を受け』は禁止」）。
_REPORT_ATTR_RE = re.compile(
    r"との報道|との観測|と報じ|報道が浮上|検討が浮上|浮上との報道|観測が浮上|報道されたこと")

# 自社決算を当日の上昇"要因"として帰属する近接構文（決算系語→同一文内15字以内→帰属語）。自社の
# 決算・業績修正が窓外（当日15:30ちょうど以降＝今夜のPTS材料、または前営業日より前の旧材料）なのに
# 当日要因として書くと時系列が成立しない（ローツェ〔6323〕型・AGENTS.md「要因帰属の規律」）。近接に
# 絞るのは、「決算は引け後＝翌日材料」と正しく位置づけつつ別文で『米株高を"受け"』等と書く正当な本文を
# 誤検知しないため（決算語と帰属語が同一文で隣接するのが誤帰属の型）。窓内に決算開示が在れば
# （`disclosures[].title` が _EARNINGS_DISC_RE に一致）正当なので発火しない。
_EARNINGS_DRIVER_RE = re.compile(
    r"(?:決算|好決算|決算短信|決算発表|本決算|四半期決算|通期決算|経常|営業利益|純利益|増益|好業績)"
    r"[^。]{0,15}(?:好感|受け|材料視|評価|好調|サプライズ)")
_EARNINGS_DISC_RE = re.compile(
    r"決算短信|業績予想|配当予想|通期|四半期|決算|上方修正|下方修正|業績修正")

# 日付トークン抽出（NFKC 済みテキストに適用）。年は落として (月,日) で比較する
# （factor 本文は「6/30」「7/1」を年無しで書き、disclosures[].date は "YYYY-MM-DD" のため）。
_YMD_RE = re.compile(r"(?<!\d)20\d{2}[-/](\d{1,2})[-/](\d{1,2})(?!\d)")   # 2026-07-02 / 2026/7/2
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)")                  # 6/30, 7/1
_KANJI_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")                          # 6月16日

FIX_HINT = ("主張を削らず裏取りする：窓内 TDnet は[開示]、株探レーティング/EDINET(大量保有/TOB)は"
            "[報道]で一次URLを本文に明記、窓外・継続材料は[テーマ]で起点報道日を明記。断定的な因果は"
            "連想・連れ高・並走 等の推定表現に。AGENTS.md「変動要因の品質規律」「要因帰属の規律」")

FIVE_PASS_KEYS = ("disclosures", "kabutan_news", "web_search", "sector_cluster", "edinet")
PASS_STATES = {"done", "na", "unavailable"}
NOT_FOUND_STATUSES = {"not_found", "unresolved"}
_NOT_FOUND_FACTOR_RE = re.compile(
    r"材料未確認|材料(?:は|を)?確認でき(?:ず|ない)|直接材料は特定でき(?:ず|ない)|5パス確認済み")

_WARNING_RULES = (
    ("旧スキーマ", "RANK_LEGACY_ITEMS"),
    ("factor_kind が空", "RANK_FACTOR_KIND_REQUIRED"),
    ("既定外", "RANK_FACTOR_KIND_INVALID"),
    ("factor が空", "RANK_FACTOR_REQUIRED"),
    ("材料日付のドリフト", "RANK_DISCLOSURE_DATE_DRIFT"),
    ("因果表現", "RANK_UNSOURCED_CAUSAL"),
    ("直接材料の帰属", "RANK_UNSOURCED_DIRECT_MATERIAL"),
    ("報道帰属", "RANK_UNSOURCED_REPORT"),
    ("自社決算を上昇要因", "RANK_EARNINGS_OUT_OF_WINDOW"),
    ("自社決算が要因", "RANK_SELF_EARNINGS_HEDGE"),
    ("精密イベント", "RANK_PRECISE_EVENT_SOURCE"),
)


def _evidence_items(evidence):
    if isinstance(evidence, list):
        return [item for item in evidence if isinstance(item, dict)]
    if not isinstance(evidence, dict):
        return []
    for key in ("items", "results", "entries", "rows"):
        value = evidence.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    by_code = evidence.get("by_code")
    if isinstance(by_code, dict):
        return [dict(value, code=value.get("code") or code)
                for code, value in by_code.items() if isinstance(value, dict)]
    return []


def _check_state(value):
    if isinstance(value, dict):
        value = value.get("status") or value.get("state")
    return str(value or "").strip().lower().replace("-", "_")


def _warning_finding(text):
    path, separator, message = text.partition(": ")
    if not separator:
        path, message = "$", text
    code_match = re.search(r"\(([^/()]+)/", path)
    rule_id = "RANK_QUALITY_WARNING"
    for marker, candidate in _WARNING_RULES:
        if marker in message:
            rule_id = candidate
            break
    return vmq.finding(path, rule_id, "WARN", message,
                       code_match.group(1) if code_match else None)

FactorUnit = collections.namedtuple(
    "FactorUnit", "path code name factor kind disclosures kabutan_news")


def _nfkc(v):
    return unicodedata.normalize("NFKC", v) if isinstance(v, str) else ""


def _norm_kind(v):
    """factor_kind を正規化：NFKC → 前後空白/角括弧を除去（既定は無括弧 "開示" 等）。"""
    if not isinstance(v, str):
        return ""
    return _nfkc(v).strip().strip("[]()【】「」").strip()


def _md(mo, dy):
    """(月,日) タプルを返す。1<=月<=12 かつ 1<=日<=31 のときのみ、それ以外は None。"""
    try:
        mo, dy = int(mo), int(dy)
    except (TypeError, ValueError):
        return None
    return (mo, dy) if 1 <= mo <= 12 and 1 <= dy <= 31 else None


def _iso_md(s):
    """"YYYY-MM-DD" → (月,日)。不正/None は None。"""
    if not isinstance(s, str):
        return None
    parts = s.split("-")
    return _md(parts[1], parts[2]) if len(parts) == 3 else None


def _fmt_md(mds):
    return "・".join("%d/%d" % (m, d) for m, d in sorted(mds))


def md_tokens(text):
    """NFKC 済みテキストから (月,日) 集合を抽出する（YYYY-MM-DD／M/D／M月D日）。"""
    norm = _nfkc(text)
    out = set()
    for rx in (_YMD_RE, _MD_RE, _KANJI_RE):
        for m in rx.finditer(norm):
            t = _md(m.group(1), m.group(2))
            if t:
                out.add(t)
    return out


def ref_md(doc, unit):
    """照合先の (月,日) 集合＝{session_date, prev_date} ∪ {disclosures[].date}。"""
    refs = set()
    for k in ("session_date", "prev_date"):
        t = _iso_md(doc.get(k))
        if t:
            refs.add(t)
    for d in unit.disclosures:
        t = _iso_md(d.get("date"))
        if t:
            refs.add(t)
    return refs


def rows_of(doc):
    """("rows", [...]) 優先、無ければ ("items", [...])、どちらも無ければ (None, [])。"""
    if isinstance(doc.get("rows"), list):
        return ("rows", doc["rows"])
    if isinstance(doc.get("items"), list):
        return ("items", doc["items"])
    return (None, [])


def iter_factor_units(doc):
    """ランク行を FactorUnit で列挙する（vmq.iter_text_units のランキング版）。"""
    key, rows = rows_of(doc)
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        factor = r.get("factor")
        yield FactorUnit(
            path="%s[%d](%s/%s)" % (key, i, r.get("code"), r.get("name")),
            code=r.get("code"), name=r.get("name"),
            factor=factor if isinstance(factor, str) else "",
            kind=_norm_kind(r.get("factor_kind")),
            disclosures=[d for d in (r.get("disclosures") or []) if isinstance(d, dict)],
            kabutan_news=[n for n in (r.get("kabutan_news") or []) if n])


def _banned(url):
    """禁止ランディングページURLに一致すれば説明文を、しなければ None を返す。"""
    if not url:
        return None
    for pat, desc in vmq.BANNED_URL_PATTERNS:
        if pat.search(url):
            return desc
    return None


def audit_ranking(doc, evidence=None):
    """Return machine-readable ERROR findings, optionally enforcing evidence.v1."""
    key, _rows = rows_of(doc)
    if key is None:
        return [vmq.finding(
            "$", "RANK_ROWS_REQUIRED", "ERROR",
            "配信JSONに rows も items も無い（構造破損。先に生成器を直す）")]

    findings = []
    for u in iter_factor_units(doc):
        # 1) 開示タグは窓内 TDnet 開示が必須
        if u.kind == "開示" and not u.disclosures:
            findings.append(vmq.finding(
                u.path + ".factor_kind", "RANK_DISCLOSURE_TAG_REQUIRES_TDNET", "ERROR",
                "factor_kind=開示 だが disclosures[] が空（窓内TDnet開示なし）。"
                "証券レーティング・EDINET(大量保有/TOB)は[報道]、窓外・継続材料は[テーマ]に",
                u.code))
        # 6) 禁止URL（disclosures の pdf_url ＋ factor 内 Markdown リンク）
        for j, d in enumerate(u.disclosures):
            desc = _banned(d.get("pdf_url"))
            if desc:
                findings.append(vmq.finding(
                    "%s.disclosures[%d].pdf_url" % (u.path, j), "RANK_BANNED_URL", "ERROR",
                    "ランディングページ出典は禁止（%s）: %s — 具体記事・TDnet/EDINET・会社IRへ"
                    % (desc, d.get("pdf_url")), u.code))
        for _label, url in vmq.MD_LINK_RE.findall(u.factor):
            desc = _banned(url)
            if desc:
                findings.append(vmq.finding(
                    u.path + ".factor", "RANK_BANNED_URL", "ERROR",
                    "ランディングページ出典は禁止（%s）: %s — 具体記事・TDnet/EDINET・会社IRへ"
                    % (desc, url), u.code))

    # Legacy public JSON remains valid when no evidence is supplied.  Supplying the
    # private ledger opts into the stronger not-found gate: every research pass must
    # be explicitly done, not applicable, or unavailable.
    if evidence is not None:
        if not isinstance(evidence, (dict, list)):
            findings.append(vmq.finding(
                "$", "RANK_EVIDENCE_STRUCTURE", "ERROR", "evidence は JSON object/array であるべき"))
            return findings
        if isinstance(evidence, dict):
            evidence_session = evidence.get("session_date")
            if evidence_session and evidence_session != doc.get("session_date"):
                findings.append(vmq.finding(
                    "$", "RANK_EVIDENCE_SESSION_MISMATCH", "ERROR",
                    "evidence.session_date(%s) が ranking.session_date(%s) と不一致"
                    % (evidence_session, doc.get("session_date"))))
        evidence_by_code = {
            str(item.get("code") or "").strip(): item for item in _evidence_items(evidence)
            if str(item.get("code") or "").strip()
        }
        for u in iter_factor_units(doc):
            item = evidence_by_code.get(_code_for_evidence(u.code))
            explicit_not_found = bool(_NOT_FOUND_FACTOR_RE.search(u.factor))
            if item is None:
                if explicit_not_found:
                    findings.append(vmq.finding(
                        u.path + ".factor", "RANK_NOT_FOUND_EVIDENCE_REQUIRED", "ERROR",
                        "材料未確認の行に対応する evidence item が無い", u.code))
                continue
            status = str(item.get("status") or "").strip().lower().replace("-", "_")
            if status not in NOT_FOUND_STATUSES and not explicit_not_found:
                continue
            checks = item.get("checks") or item.get("five_passes") or {}
            incomplete = [name for name in FIVE_PASS_KEYS
                          if _check_state(checks.get(name) if isinstance(checks, dict) else None)
                          not in PASS_STATES]
            if incomplete:
                findings.append(vmq.finding(
                    u.path + ".factor", "RANK_NOT_FOUND_FIVE_PASS_INCOMPLETE", "ERROR",
                    "not_found/unresolved は5パスの全状態が done/na/unavailable 必須（未完了: %s）"
                    % "・".join(incomplete), u.code))
    return findings


def _code_for_evidence(value):
    return str(value or "").strip()


def check_ranking(doc, evidence=None):
    """Backward-compatible ERROR strings; evidence is an optional strict input."""
    return [vmq._human_finding(item) for item in audit_ranking(doc, evidence=evidence)]


def check_ranking_warnings(doc):
    """因果・日付・帰属・hygiene の監査（WARN・exit code に影響しない）。純粋関数。"""
    key, rows = rows_of(doc)
    if key is None:
        return []

    warnings = []
    if key == "items":
        warnings.append("配信JSONが旧スキーマ 'items'（2026-06-18型）。以降の 'rows' 型へ移行を"
                        "（画面・監査で取りこぼしやすい）")

    for u in iter_factor_units(doc):
        norm = _nfkc(u.factor)
        has_link = bool(vmq.MD_LINK_RE.search(u.factor))
        has_hedge = any(h in norm for h in vmq.HEDGE_MARKERS)
        has_owndata = any(o in norm for o in vmq.OWN_DATA_MARKERS)
        open_disc = u.kind == "開示" and bool(u.disclosures)

        # 5) factor_kind / factor の hygiene
        if not u.kind:
            warnings.append("%s: factor_kind が空。[開示/報道/テーマ]のいずれかを付ける" % u.path)
        elif u.kind not in VALID_KINDS:
            warnings.append("%s: factor_kind='%s' は既定外（[開示/報道/テーマ]のみ）" % (u.path, u.kind))
        if not u.factor.strip():
            warnings.append("%s: factor が空（材料未確認なら『当日固有の材料は確認できず』等と明記する）" % u.path)

        # 2) 開示の日付ドリフト
        if u.kind == "開示" and u.disclosures:
            toks = md_tokens(u.factor)
            refs = ref_md(doc, u)
            if toks and toks.isdisjoint(refs):
                warnings.append("%s: factor_kind=開示 で日付（%s）を挙げるが disclosures/session/prev（%s）の"
                                "どれとも一致しない（材料日付のドリフト疑い）"
                                % (u.path, _fmt_md(toks), _fmt_md(refs)))

        # 3) 無出典の因果語・直接材料帰属
        causal_hits = [w for w in vmq.CAUSAL_WORDS if w in norm]
        tier_a_hits = sorted({m.group(0) for m in vmq.TIER_A_RE.finditer(norm)})
        if (causal_hits or tier_a_hits) and not (open_disc or has_link or has_hedge or has_owndata):
            if causal_hits:
                warnings.append("%s: 因果表現（%s）に出典（開示PDF/インラインリンク）も推定マーカーも無い"
                                "（連想・連れ高・並走 等の推定表現に／出典を足す）"
                                % (u.path, "・".join(causal_hits)))
            if tier_a_hits:
                warnings.append("%s: 直接材料の帰属（%s）に出典も推定マーカーも無い"
                                "（一次出典を足す／推定表現に）" % (u.path, "・".join(tier_a_hits)))

        # 3b) 無出典の報道帰属（「〜との報道」等・因果語にも「〜を受け」にも該当しない具体報道の断定）
        if _REPORT_ATTR_RE.search(norm) and not (
                u.disclosures or u.kabutan_news or has_link or has_hedge or has_owndata):
            warnings.append("%s: 報道帰属（「〜との報道」等）に出典も推定マーカーも無い"
                            "（一次/良質報道の記事URLと起点報道日を本文に明記／連想・とみられる 等の推定表現に）"
                            % u.path)

        # 3c) 自社決算を要因に挙げるが窓内に決算開示が無い＝決算が窓外（当日15:30以降＝翌日材料、
        #     または前営業日より前の旧材料）で当日の日中要因にできない（ローツェ〔6323〕型）。
        #     あわせて、自社の確定材料に「連想（買い）」を使う矛盾も監査する。
        has_earn_driver = bool(_EARNINGS_DRIVER_RE.search(norm))
        if has_earn_driver:
            has_inwindow_earn_disc = any(
                _EARNINGS_DISC_RE.search(_nfkc(d.get("title", ""))) for d in u.disclosures)
            if not has_inwindow_earn_disc:
                warnings.append("%s: 自社決算を上昇要因に挙げるが窓内(前営業日15:30以降∪当日15:30未満)"
                                "に決算開示が無い（決算が当日15:30以降＝翌日材料か、前営業日引け後なら"
                                "「前日(日付)引け後」と開示日を明記を。AGENTS.md 要因帰属の規律）" % u.path)
            if "連想" in norm:
                warnings.append("%s: 自社決算が要因の行に「連想（買い）」（自社の確定材料は直接の"
                                "好感買い。連想は他社材料・テーマ波及に限る）" % u.path)

        # 4) 報道タグの企業アクション語が無裏付け（開示は check1、テーマは同業引用が正当なので対象外）
        if u.kind == "報道":
            hits = [t for t in RANKING_CLAIM_TRIGGERS if t in norm]
            if hits and not (u.disclosures or u.kabutan_news or has_link or has_hedge):
                warnings.append("%s: 精密イベント（%s）を挙げるが disclosures・kabutan_news・"
                                "インラインリンク・推定マーカーのいずれの裏付けも無い（一次/株探/EDINETで裏取り）"
                                % (u.path, "・".join(hits)))
    return warnings


def audit_ranking_warnings(doc):
    """Machine-readable view of the backward-compatible warning audit."""
    return [_warning_finding(text) for text in check_ranking_warnings(doc)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="値上がり率ランキング JSON の変動要因の出典品質検証")
    ap.add_argument("paths", nargs="+", help="docs/data/<date>.json（複数可）")
    ap.add_argument("--evidence", default=None,
                    help="private evidence.v1。指定時のみ not_found 5パスを厳格検査")
    ap.add_argument("--format", choices=("human", "json"), default="human",
                    dest="output_format", help="出力形式（既定 human）")
    ap.add_argument("--repair-targets", nargs="?", const="-", default=None, metavar="PATH",
                    help="再調査すべき code/path を JSON 出力（PATH省略時はstdout）")
    args = ap.parse_args(argv)

    all_errors = []
    file_results = []
    evidence = None
    evidence_error = None
    if args.evidence:
        try:
            with open(args.evidence, "r", encoding="utf-8") as handle:
                evidence = json.load(handle)
        except (OSError, ValueError) as exc:
            evidence_error = vmq.finding(
                "$", "IO_EVIDENCE_READ_ERROR", "ERROR", "evidence 読み込み失敗: %s" % exc)

    for p in args.paths:
        name = os.path.basename(p)
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            item = vmq.finding("$", "IO_READ_ERROR", "ERROR", "読み込み失敗: %s" % e)
            all_errors.append("%s: %s" % (name, vmq._human_finding(item)))
            file_results.append({"file": p, "findings": [item]})
            continue
        errors = ([evidence_error] if evidence_error else audit_ranking(doc, evidence=evidence))
        warnings = audit_ranking_warnings(doc) if (
            not errors or args.output_format == "json" or args.repair_targets) else []
        file_results.append({"file": p, "findings": errors + warnings})
        if errors:
            all_errors.extend("%s: %s" % (name, vmq._human_finding(item)) for item in errors)
        else:
            if args.output_format == "human" and not args.repair_targets:
                for item in warnings:
                    sys.stderr.write("[validate_ranking_quality] WARN: %s: %s\n" % (
                        name, vmq._human_finding(item)))
                key, rows = rows_of(doc)
                kinds = collections.Counter(_norm_kind(r.get("factor_kind"))
                                            for r in rows if isinstance(r, dict))
                sys.stderr.write("[validate_ranking_quality] OK: %s（%s %d行／開示%d 報道%d テーマ%d%s）\n"
                                 % (name, key, len(rows), kinds.get("開示", 0), kinds.get("報道", 0),
                                    kinds.get("テーマ", 0),
                                    "・WARN %d件" % len(warnings) if warnings else ""))

    if args.repair_targets:
        repair_payload = {
            "schema_version": vmq.FINDINGS_SCHEMA_VERSION,
            "validator": "ranking",
            "files": [{"file": result["file"],
                       "targets": vmq.select_repair_targets(result["findings"], prefer_code=True)}
                      for result in file_results],
        }
        if args.repair_targets == "-":
            json.dump(repair_payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            vmq.write_json_atomic(args.repair_targets, repair_payload)
    if args.output_format == "json" and args.repair_targets != "-":
        payload = {
            "schema_version": vmq.FINDINGS_SCHEMA_VERSION,
            "validator": "ranking",
            "files": file_results,
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    if all_errors:
        if args.output_format == "human" and not args.repair_targets:
            sys.stderr.write("[validate_ranking_quality] ERROR: %d件\n%s\n修正方針: %s\n"
                             % (len(all_errors), "\n".join("  - " + e for e in all_errors), FIX_HINT))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
