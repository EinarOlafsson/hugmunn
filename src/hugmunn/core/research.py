"""Research tools: literature search and local document reading.

``web_search`` finds pages; these return *structured records* — title, authors,
journal, year, DOI, abstract. For a literature question that difference matters:
a scraped snippet cannot be cited, and a model asked to produce a reference from
one will invent the missing fields.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .tools import ToolError, _safe_path
# web._clip takes a length limit; tools._clip does not. These tools need the
# caller-supplied max_chars, so use web's.
from .web import TIMEOUT, UA, WebError, _clip

# export.arxiv.org is routinely slower than the default; it rate-limits by IP
# and can take 30s+ to answer a cold query.
ARXIV_TIMEOUT = 90.0

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _raise_for_status(response: httpx.Response, service: str) -> None:
    """Turn an HTTP failure into a message the model can act on.

    Without this a 429 body reaches the XML parser and surfaces as
    'syntax error: line 1, column 0', which tells nobody anything. Rate limits
    are the common failure for both of these APIs, so they get their own advice.
    """
    if response.status_code == 429:
        raise WebError(
            f"{service} is rate-limiting this machine (HTTP 429). Wait a minute "
            "and retry, or reduce how often you search."
        )
    if response.status_code >= 400:
        raise WebError(f"{service} returned HTTP {response.status_code}: "
                       f"{response.text[:200]}")


def pubmed_search(_workdir: str, query: str, max_results: int = 8) -> str:
    """Search PubMed and return structured records with PMIDs and abstracts."""
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
            search = client.get(
                f"{EUTILS}/esearch.fcgi",
                params={"db": "pubmed", "term": query, "retmax": int(max_results),
                        "retmode": "json", "sort": "relevance"},
            )
            _raise_for_status(search, "PubMed")
            ids = search.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return f"No PubMed results for {query!r}."
            fetch = client.get(
                f"{EUTILS}/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            )
            _raise_for_status(fetch, "PubMed")
            xml = fetch.text
    except httpx.HTTPError as exc:
        raise WebError(f"PubMed request failed: {exc}") from exc
    except ValueError as exc:
        raise WebError(f"PubMed returned unparseable data: {exc}") from exc

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise WebError(f"could not parse PubMed XML: {exc}") from exc

    entries = []
    for article in root.findall(".//PubmedArticle"):
        def text(path: str, default: str = "") -> str:
            node = article.find(path)
            return "".join(node.itertext()).strip() if node is not None else default

        pmid = text(".//PMID")
        authors = [
            f"{a.findtext('LastName', '')} {a.findtext('Initials', '')}".strip()
            for a in article.findall(".//Author")[:4]
        ]
        byline = ", ".join(a for a in authors if a)
        if len(article.findall(".//Author")) > 4:
            byline += " et al."
        abstract = " ".join(
            "".join(n.itertext()).strip() for n in article.findall(".//AbstractText")
        )
        doi = ""
        for ident in article.findall(".//ArticleId"):
            if ident.get("IdType") == "doi":
                doi = (ident.text or "").strip()
        entries.append(
            f"{len(entries) + 1}. {text('.//ArticleTitle', '(untitled)')}\n"
            f"   {byline or '(no authors listed)'}\n"
            f"   {text('.//Journal/Title')} ({text('.//PubDate/Year', 'n.d.')})\n"
            f"   PMID {pmid}"
            + (f" · doi:{doi}" if doi else "")
            + f"\n   https://pubmed.ncbi.nlm.nih.gov/{pmid}/\n"
            + (f"   {abstract[:600]}{'…' if len(abstract) > 600 else ''}" if abstract else "   (no abstract)")
        )
    return _clip(f"PubMed results for {query!r}:\n\n" + "\n\n".join(entries))


def arxiv_search(_workdir: str, query: str, max_results: int = 8) -> str:
    """Search arXiv and return structured records with abstracts and PDF links."""
    try:
        with httpx.Client(timeout=ARXIV_TIMEOUT, headers={"User-Agent": UA}) as client:
            response = client.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f"all:{query}", "max_results": int(max_results),
                        "sortBy": "relevance"},
            )
            _raise_for_status(response, "arXiv")
            feed = response.text
    except httpx.HTTPError as exc:
        raise WebError(f"arXiv request failed: {exc}") from exc

    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(feed)
    except ET.ParseError as exc:
        raise WebError(f"could not parse arXiv feed: {exc}") from exc

    entries = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("a:summary", "", ns) or "").strip().replace("\n", " ")
        authors = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)[:4]]
        link = entry.findtext("a:id", "", ns) or ""
        published = (entry.findtext("a:published", "", ns) or "")[:10]
        pdf = link.replace("/abs/", "/pdf/")
        entries.append(
            f"{len(entries) + 1}. {title}\n"
            f"   {', '.join(a for a in authors if a)}\n"
            f"   {published}\n   {link}\n   PDF: {pdf}\n"
            f"   {summary[:500]}{'…' if len(summary) > 500 else ''}"
        )
    if not entries:
        return f"No arXiv results for {query!r}."
    return _clip(f"arXiv results for {query!r}:\n\n" + "\n\n".join(entries))


_PDF_TEXT = re.compile(rb"\((?:[^()\\]|\\.)*\)")


def read_pdf(workdir: str, path: str, max_chars: int = 12_000) -> str:
    """Extract text from a local PDF.

    Prefers ``pypdf`` when installed; otherwise falls back to ``pdftotext`` from
    poppler, which is present on most Linux systems. Reports honestly when
    neither is available rather than returning mangled bytes.
    """
    target = _safe_path(workdir, path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")

    try:
        import pypdf  # noqa: PLC0415 - optional dependency, probed at call time

        reader = pypdf.PdfReader(str(target))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        header = f"{target.name} — {len(reader.pages)} pages (pypdf)\n{'-' * 60}\n"
        return _clip(header + text.strip(), int(max_chars))
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - a corrupt PDF must not crash the loop
        raise ToolError(f"pypdf could not read {path}: {exc}") from exc

    import shutil
    import subprocess

    if shutil.which("pdftotext"):
        try:
            proc = subprocess.run(
                ["pdftotext", "-q", str(target), "-"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ToolError(f"pdftotext failed: {exc}") from exc
        if proc.stdout.strip():
            header = f"{target.name} (pdftotext)\n{'-' * 60}\n"
            return _clip(header + proc.stdout.strip(), int(max_chars))
        raise ToolError(f"{path} produced no extractable text — it may be scanned images.")

    raise ToolError(
        "no PDF text extractor available. Install one with "
        "`pip install pypdf` or `apt install poppler-utils`."
    )


def image_info(workdir: str, path: str) -> str:
    """Dimensions, mode, and metadata of a local image — including TIFF pages."""
    target = _safe_path(workdir, path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")
    try:
        from PIL import Image  # noqa: PLC0415 - optional dependency

        with Image.open(target) as img:
            frames = getattr(img, "n_frames", 1)
            rows = [
                f"{target.name}",
                f"  format:     {img.format}",
                f"  size:       {img.width} x {img.height}",
                f"  mode:       {img.mode}   (bit depth / channels)",
                f"  frames:     {frames}" + ("   (multi-page TIFF / z-stack)" if frames > 1 else ""),
                f"  file size:  {target.stat().st_size:,} B",
            ]
            info = {k: v for k, v in (img.info or {}).items() if isinstance(v, (str, int, float))}
            if info:
                rows.append("  metadata:   " + ", ".join(f"{k}={v}" for k, v in list(info.items())[:8]))
            return "\n".join(rows)
    except ImportError:
        raise ToolError("Pillow is not installed — `pip install pillow`") from None
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"could not read {path}: {exc}") from exc
