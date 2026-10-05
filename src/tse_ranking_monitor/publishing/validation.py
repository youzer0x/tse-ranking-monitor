"""Publication checks shared by the local builder and trusted promotion code."""

from ..quality import ranking as ranking_quality, market as market_quality
from ..research.evidence import compile_research_results
from ..research.plan import ranking_input_digest
from ..contracts import validate_market_numbers


def require_ranking_quality(ranking, evidence=None):
    findings = ranking_quality.audit_ranking(ranking, evidence=evidence)
    findings += ranking_quality.audit_ranking_warnings(ranking)
    if findings:
        raise ValueError("ranking quality failed: " + "; ".join(
            f"{item['rule_id']}: {item['message']}" for item in findings))


def require_market_quality(market, session):
    validate_market_numbers(market)
    if not isinstance(market, dict) or market.get("schema_version") != 1:
        raise ValueError("market schema_version must be 1")
    if market.get("session_date") != session:
        raise ValueError("market session_date does not match ranking")
    try:
        findings = market_quality.audit_doc(market)
        if not findings:
            findings = market_quality.audit_warnings(market)
    except (TypeError, AttributeError, KeyError) as exc:
        raise ValueError("market has invalid nested fields") from exc
    if findings:
        raise ValueError("market quality failed: " + "; ".join(
            f"{item['rule_id']}: {item['message']}" for item in findings))


def require_compiled_evidence(ranking, research_dir):
    if research_dir is None:
        raise ValueError("research-dir is required before publication")
    # Recompile the source results; a manually edited evidence.json is not proof.
    evidence, factors = compile_research_results(research_dir, strict=True)
    if evidence.get("session_date") != ranking.get("session_date"):
        raise ValueError("research session_date does not match ranking")
    if evidence.get("ranking_digest") != ranking_input_digest(ranking):
        raise ValueError("ranking facts do not match the researched Stage1 input")
    actual = [{key: row.get(key) for key in ("code", "factor", "factor_kind")}
              for row in ranking["rows"]]
    if actual != factors:
        raise ValueError("ranking factors do not match strictly compiled research")
    require_ranking_quality(ranking, evidence)
    return evidence
