"""Web access tools: search and fetch.

Split out from ``tools.py`` because these are the only tools that reach outside
the machine, and that changes the threat model. Three constraints follow:

* **Private networks are blocked.** A model-supplied URL is resolved and every
  resolved address checked against loopback/link-local/private ranges before a
  connection is made. Without that, ``web_fetch`` is an SSRF primitive pointed
  at the LAN — cloud metadata endpoints, the router admin page, internal
  services. This is the reason for the DNS-then-check dance rather than a
  simple hostname string test.
* **Responses are capped and truncated** so a large page can't blow a 32K
  context window.
* **HTML is reduced to text** before it reaches the model; markup is noise that
  costs tokens and degrades comprehension.
"""

from __future__ import annotations

import html as html_mod
import ipaddress
import re
import socket
from urllib.parse import quote_plus, urlparse

import httpx

MAX_PAGE_CHARS = 15_000
MAX_BYTES = 5 * 1024 * 1024  # stop reading a response past this
TIMEOUT = 25.0
UA = "Mozilla/5.0 (X11; Linux x86_64) localagent/0.1"


class WebError(Exception):
    """Recoverable failure; the message is fed back to the model."""


# ------------------------------------------------------------------ safety


def _assert_public(url: str) -> str:
    """Reject anything that isn't a public http(s) address.

    Resolves the hostname and checks *every* address it maps to — a name can
    resolve to several, and checking only the first leaves a hole.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebError(f"only http/https URLs are allowed, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise WebError(f"could not parse a hostname out of {url!r}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebError(f"could not resolve {host!r}: {exc}") from exc

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise WebError(
                f"{host!r} resolves to the non-public address {addr} — refusing. "
                "Web tools reach the public internet only; use read_file for "
                "anything on this machine or network."
            )
    return url


# ------------------------------------------------------------- html → text

_DROP_BLOCKS = re.compile(
    r"<(script|style|noscript|svg|head|nav|footer|form)\b.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_BREAKS = re.compile(r"</(p|div|li|tr|h[1-6]|section|article|br)\s*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]{2,}")


def html_to_text(raw: str) -> str:
    """Crude but dependency-free HTML reduction. Good enough for reading prose."""
    text = _DROP_BLOCKS.sub(" ", raw)
    text = _BREAKS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = html_mod.unescape(text)
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


def _clip(text: str, limit: int = MAX_PAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} more characters]"


# ------------------------------------------------------------------- tools


def web_fetch(_workdir: str, url: str, max_chars: int = MAX_PAGE_CHARS) -> str:
    """Fetch a public URL and return its readable text."""
    _assert_public(url)
    try:
        with httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise WebError(f"HTTP {response.status_code} fetching {url}")
                # Redirects can land somewhere private; re-check the final URL.
                _assert_public(str(response.url))
                ctype = response.headers.get("content-type", "")
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_BYTES:
                        break
                body = b"".join(chunks).decode("utf-8", "replace")
    except httpx.HTTPError as exc:
        raise WebError(f"request failed: {exc}") from exc

    text = html_to_text(body) if "html" in ctype.lower() else body
    header = f"{response.url}\n{'-' * 60}\n"
    return header + _clip(text, int(max_chars))


_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def web_search(_workdir: str, query: str, max_results: int = 8) -> str:
    """Search the web via DuckDuckGo's HTML endpoint (no API key required)."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        with httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            response = client.post(
                "https://html.duckduckgo.com/html/", data={"q": query}
            )
            if response.status_code >= 400:
                raise WebError(f"HTTP {response.status_code} from search backend")
            body = response.text
    except httpx.HTTPError as exc:
        raise WebError(f"search failed: {exc}") from exc

    rows: list[str] = []
    for match in _RESULT.finditer(body):
        title = html_to_text(match["title"])
        snippet = html_to_text(match["snippet"])
        link = html_mod.unescape(match["url"])
        # DDG wraps results in a redirector; pull the real target out.
        if "uddg=" in link:
            from urllib.parse import parse_qs, unquote

            target = parse_qs(urlparse(link).query).get("uddg")
            if target:
                link = unquote(target[0])
        rows.append(f"{len(rows) + 1}. {title}\n   {link}\n   {snippet}")
        if len(rows) >= int(max_results):
            break

    if not rows:
        return (
            f"No results parsed for {query!r}. The search backend may have "
            "changed its markup, or the query returned nothing."
        )
    return _clip(f"Results for {query!r}:\n\n" + "\n\n".join(rows))
