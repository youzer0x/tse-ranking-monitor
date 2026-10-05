"""Safe plain text and the limited inline link syntax shared by outputs."""

import html
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def http_url(value):
    if not isinstance(value, str) or re.search(r"[\s<>\"'\\]", value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def escape(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def inline_html(value):
    value = str(value if value is not None else "")
    out, start = [], 0
    for match in LINK.finditer(value):
        out.append(escape(value[start:match.start()]))
        label, url = match.groups()
        out.append(f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(label)}</a>'
                   if http_url(url) else escape(match.group()))
        start = match.end()
    out.append(escape(value[start:]))
    return "".join(out)


def session_url(pages_url, session):
    if not http_url(pages_url):
        raise ValueError("pages-url must be an absolute http(s) URL")
    parts = urlsplit(pages_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "date"]
    query.append(("date", session))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
