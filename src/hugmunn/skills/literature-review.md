---
name: Literature review
category: Science
description: Searching, reading, and citing papers without inventing references.
when: searching for, reading, or citing papers.
default: false
---

Use `pubmed_search` and `arxiv_search` rather than `web_search` for anything
you intend to cite. They return real titles, authors, journals, years, PMIDs
and DOIs. A web snippet gives you none of those, and a reference assembled from
a snippet is a reference you partly invented.

**Never produce a citation you did not retrieve.** No DOI, PMID, author list,
or year from memory. If you know work exists but not the reference, say exactly
that — it is useful, and a plausible fake is not.

Search the way papers are written. Field terms, not questions: "bradyzoite
differentiation stress response" rather than "how do parasites become
dormant?". Try the synonyms a field actually uses — cyst / bradyzoite /
chronic stage are the same literature under different words.

Read the abstract before deciding a paper is relevant, and the paper before
claiming what it found. `download_pdfs` then `read_pdf` gets you the real text.
Abstracts overstate; methods and figures are where the claim is actually
supported or not.

Distinguish what a paper *showed* from what it *concluded*. The discussion
routinely reaches past the data. When you summarise, attribute the finding to
the evidence: "in HFF cells at 48h" is part of the result, not a detail.

Note the study system every time. A result in tachyzoites may not hold in
bradyzoites; in a type I strain may not hold in type II; in mouse may not hold
in human. Most contradictions in a literature dissolve once the systems are
compared properly.

Prefer primary sources. A review's characterisation of a result is a
second-hand reading — useful for orientation, not for a claim you are making.

When sources conflict, say so and give both, with what differs between them.
Silently picking the one that fits the narrative is the most common way a
literature summary misleads.
