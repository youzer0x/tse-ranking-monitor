"""Real Chromium interaction tests, served entirely through local route fixtures."""

import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tse_ranking_monitor.publishing.render import generate_pages_html

pytestmark = pytest.mark.enable_socket


def ranking(day, name="テスト銘柄"):
    return {"session_date": day, "counts": {"qualifying": 1}, "rows": [
        {"rank": 1, "code": "7000", "name": name, "close": 1000, "pct": 8.5,
         "mcap_oku": 12345, "turnover_yen": 10000000, "mcap_source": "yahoo",
         "factor_kind": "テーマ", "factor": "同業株と並走したとみられる。"}]}


@pytest.fixture
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        yield page
        browser.close()


def serve(page, *, handler=None, docs=None, url="https://dashboard.test/"):
    docs = docs or {"2026-07-15": ranking("2026-07-15"), "2026-07-14": ranking("2026-07-14")}
    def route(request):
        path = urlparse(request.request.url).path
        if handler and handler(request, path):
            return
        if path == "/":
            request.fulfill(content_type="text/html", body=generate_pages_html())
        elif path == "/data/manifest.json":
            request.fulfill(json={"dates": list(docs)})
        elif path.startswith("/data/") and path.endswith(".json") and path[6:-5] in docs:
            request.fulfill(json=docs[path[6:-5]])
        else:
            request.fulfill(status=404, body="")
    page.route("**/*", route)
    page.goto(url)


def test_latest_date_wins_when_previous_request_is_slow(browser_page):
    page = browser_page
    held = []
    def slow(request, path):
        if path == "/data/2026-07-15.json":
            held.append(request)
            return True
    serve(page, handler=slow)
    expect(page.locator("#dateSelect")).to_have_value("2026-07-15")
    page.select_option("#dateSelect", "2026-07-14")
    expect(page.locator("#tableArea .name")).to_contain_text("テスト銘柄")
    for request in held:
        request.fulfill(json=ranking("2026-07-15", "古い日付の銘柄"))
    expect(page.locator("#dateSelect")).to_have_value("2026-07-14")
    expect(page.locator("#tableArea")).not_to_contain_text("古い日付")
    assert "date=2026-07-14" in page.url
    assert page.evaluate("data.session_date") == "2026-07-14"


def test_mobile_units_long_names_and_stock_links(browser_page):
    page = browser_page
    page.set_viewport_size({"width": 375, "height": 812})
    long_name = "長い社名も全文表示するテストホールディングス株式会社"
    serve(page, docs={"2026-07-15": ranking("2026-07-15", long_name)})
    expect(page.locator(".stock-link")).to_be_visible()
    expect(page.locator(".stock-link")).to_contain_text(long_name)
    assert page.locator(".stock-link").get_attribute("href").endswith("code=7000")
    expect(page.locator("#tableArea")).to_contain_text("1億円未満")
    expect(page.locator(".mcap")).to_have_text("12,345億円（Yahoo参照）")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    box = page.locator(".stock-link").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375
    page.screenshot(path=str(ROOT / ".work/browser-review-mobile.png"), full_page=True)


def test_slow_market_does_not_delay_ranking(browser_page):
    page = browser_page
    held = []
    def slow(request, path):
        if path.endswith("_market.json"):
            held.append(request)
            return True
    serve(page, handler=slow)
    expect(page.locator(".stock-link")).to_be_visible()
    page.locator("#tabMarket").click()
    expect(page.locator("#marketArea")).to_contain_text("読み込み中")
    for request in held:
        request.fulfill(status=404, body="")
    expect(page.locator("#marketArea")).to_contain_text("公開されていません")


def test_missing_error_retry_tabs_and_expired_date(browser_page):
    page = browser_page
    failures = {"market": True}
    def market(request, path):
        if path.endswith("_market.json") and failures["market"]:
            request.fulfill(status=503, body="Unavailable")
            return True
    serve(page, handler=market, url="https://dashboard.test/?date=2026-07-14#market")
    expect(page.locator("#marketArea")).to_contain_text("読み込めませんでした")
    expect(page.locator("#tabMarket")).to_have_attribute("aria-current", "page")
    failures["market"] = False
    page.locator("#marketArea button").click()
    expect(page.locator("#marketArea")).to_contain_text("公開されていません")
    page.locator("#tabRanking").click()
    expect(page.locator("#tableArea")).to_be_visible()
    page.goto("https://dashboard.test/?date=2020-01-01")
    expect(page.locator("#tableArea")).to_contain_text("保存期間を過ぎたか")
    expect(page.locator("#summary")).to_be_empty()
    expect(page.locator("#dateSelect")).to_have_value("2020-01-01")


def test_untrusted_text_and_legacy_data_render_safely(browser_page):
    page = browser_page
    value = ranking("2026-07-15", '<img src=x onerror="window.injected=1">')
    row = value["rows"][0]
    row["code"] = '7000" onclick="window.injected=1'
    row["factor"] = '[記事](https://example.com/" onmouseover="window.injected=1) <svg onload="window.injected=1">'
    row["mcap_flag"] = '<img src=x onerror="window.injected=1">'
    row["disclosures"] = [{"pdf_url": 'javascript:alert(1)'}]
    value["items"] = value.pop("rows")  # historical alias remains readable
    serve(page, docs={"2026-07-15": value})
    expect(page.locator("#tableArea .name")).to_contain_text("<img")
    assert page.locator("#tableArea img, #tableArea svg, #tableArea [onclick]").count() == 0
    assert page.evaluate("window.injected") is None
