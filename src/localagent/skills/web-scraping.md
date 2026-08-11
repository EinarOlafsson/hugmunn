---
name: Web scraping
category: Web
description: Collecting pages, images, and PDFs at scale — rate limits, etiquette, and what breaks.
default: false
---

You have `web_search`, `image_search`, `web_fetch`, and `download_pdfs`. This
is how to use them for bulk collection without producing garbage or getting
blocked.

## Before scraping anything

Ask what the end artefact is. "Download every PDF from this lab page" and
"find the three papers about bradyzoite differentiation" need different
approaches, and the second is usually what was meant. Collecting 200 files the
user then has to sift is not a result.

Check whether an API exists first. PubMed, arXiv, Crossref, and most journals
have structured endpoints that return clean metadata. Scraping HTML when an API
exists produces worse data for more effort.

## Rate and volume

Fetch serially and leave a pause between requests. A burst of parallel requests
is the fastest way to a 429 or an IP block, and you cannot un-block yourself.

Cap collection explicitly and say what the cap was. `download_pdfs` takes
`max_files`; if there are more links than that, report the number you skipped
rather than silently truncating.

Prefer one page fetched and read carefully over ten fetched and skimmed. Each
`web_fetch` costs context; a page you did not use was wasted budget.

## Handling what comes back

Expect failures per item, not per run. A dead link, a paywall, a 403 on one PDF
out of twenty is normal — record it and continue rather than aborting the whole
job.

Deduplicate by resolved URL. The same paper is routinely linked several times
on one page, and redirects mean two different-looking URLs land in the same
place.

Verify before reporting. A "PDF" that is 4 KB is an error page; a downloaded
file that starts with `<!DOCTYPE` is HTML. Say what you actually got, with
sizes.

## Etiquette

Respect the site. Do not hammer a small academic server to save yourself a
minute, and do not scrape material behind a login or a paywall.

Attribute. When you collect from a source, keep the URL alongside the file so
provenance survives; a folder of untitled PDFs with no origin is close to
useless six months later.

If a site blocks you, stop and say so. Do not rotate user agents or work around
a refusal — report it and offer the manual route instead.
