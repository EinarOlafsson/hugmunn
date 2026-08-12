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
from pathlib import Path
import re
import socket
from urllib.parse import quote_plus, urlparse

import httpx

MAX_PAGE_CHARS = 15_000
MAX_BYTES = 5 * 1024 * 1024  # stop reading a response past this
TIMEOUT = 25.0
UA = "Mozilla/5.0 (X11; Linux x86_64) hugmunn/0.1"


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


_VQD = re.compile(r'vqd=["\']?([\d-]+)["\']?')


def image_search(_workdir: str, query: str, max_results: int = 12) -> str:
    """Image search via DuckDuckGo's JSON endpoint.

    Two requests are required: the endpoint rejects anything without a ``vqd``
    token, which is only obtainable by first loading the search page.
    """
    try:
        with httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            landing = client.get(
                "https://duckduckgo.com/", params={"q": query, "iax": "images", "ia": "images"}
            )
            match = _VQD.search(landing.text)
            if match is None:
                raise WebError(
                    "could not obtain a search token — DuckDuckGo may have "
                    "changed its page. Try web_search instead."
                )
            payload = client.get(
                "https://duckduckgo.com/i.js",
                params={"l": "us-en", "o": "json", "q": query,
                        "vqd": match.group(1), "f": ",,,", "p": "1"},
                headers={"Referer": "https://duckduckgo.com/"},
            ).json()
    except httpx.HTTPError as exc:
        raise WebError(f"image search failed: {exc}") from exc
    except ValueError as exc:
        raise WebError(f"image search returned unparseable data: {exc}") from exc

    rows = []
    for item in (payload.get("results") or [])[: int(max_results)]:
        rows.append(
            f"{len(rows) + 1}. {item.get('title', '(untitled)')}\n"
            f"   image:  {item.get('image')}\n"
            f"   page:   {item.get('url')}\n"
            f"   size:   {item.get('width')}x{item.get('height')}"
        )
    if not rows:
        return f"No images found for {query!r}."
    return _clip(f"Image results for {query!r}:\n\n" + "\n\n".join(rows))


# Matches both a .pdf extension and extensionless PDF routes like arXiv's
# /pdf/2401.12345 — the latter is common enough that an extension-only pattern
# silently finds nothing on major preprint servers.
_PDF_HREF = re.compile(
    r'href=["\']([^"\']*?(?:\.pdf(?:\?[^"\']*)?|/pdf/[^"\']+))["\']', re.IGNORECASE
)


def download_pdfs(
    workdir: str, url: str, subdir: str = "pdfs", max_files: int = 10
) -> str:
    """Find every PDF linked from a page and download them into the workdir.

    Downloads land under ``workdir`` — never an arbitrary path — because this
    tool writes files and the destination must not be model-controlled.
    """
    from urllib.parse import urljoin

    _assert_public(url)

    # Validate the destination *before* any network work, so a traversal
    # attempt fails fast and is caught even when the page has no PDF links.
    root = Path(workdir).expanduser().resolve()
    dest = (root / subdir).resolve()
    if dest != root and root not in dest.parents:
        raise WebError(
            f"destination {subdir!r} resolves outside the working directory ({root})"
        )

    try:
        with httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            page = client.get(url)
            if page.status_code >= 400:
                raise WebError(f"HTTP {page.status_code} fetching {url}")

            links, seen = [], set()
            for href in _PDF_HREF.findall(page.text):
                absolute = urljoin(str(page.url), html_mod.unescape(href))
                if absolute not in seen:
                    seen.add(absolute)
                    links.append(absolute)
            if not links:
                return f"No PDF links found on {url}"

            dest.mkdir(parents=True, exist_ok=True)

            rows, saved = [], 0
            for link in links[: int(max_files)]:
                name = Path(urlparse(link).path).name or f"file{saved}.pdf"
                name = "".join(c for c in name if c.isalnum() or c in "._-")[:120]
                try:
                    _assert_public(link)
                    resp = client.get(link)
                    if resp.status_code >= 400:
                        rows.append(f"  HTTP {resp.status_code}  {link}")
                        continue
                    body = resp.content[:MAX_BYTES]
                    (dest / name).write_bytes(body)
                    saved += 1
                    rows.append(f"  saved {name}  ({len(body) / 1e6:.1f} MB)")
                except (httpx.HTTPError, WebError, OSError) as exc:
                    rows.append(f"  failed {link}: {exc}")
    except httpx.HTTPError as exc:
        raise WebError(f"request failed: {exc}") from exc

    header = (
        f"{len(links)} PDF link(s) on {url}; downloaded {saved} to {dest}\n"
        + ("" if len(links) <= max_files else f"({len(links) - max_files} not fetched — raise max_files)\n")
    )
    return _clip(header + "\n".join(rows))


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
