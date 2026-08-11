---
name: Explaining code
category: Coding
description: Describing unfamiliar code so someone can act on it, not just recognise it.
when: asked what code does, how it works, or why it is written that way.
default: false
---

Start with what it is for, not what it does line by line. "This resolves a
model-supplied path and rejects anything outside the working directory" beats
a walkthrough of each statement — the reader can see the statements.

Say where it fits. What calls it, what it calls, what breaks if it is wrong.
Code makes sense in terms of its neighbours, and a function explained in
isolation stays confusing.

Read the surrounding code before explaining a fragment. Much of what looks
wrong or arbitrary in isolation is correct in context, and saying so
confidently is worse than saying nothing.

Name the non-obvious decision. Every codebase has lines that look odd and are
load-bearing — a guard against a specific failure, an ordering that matters, a
workaround for a library bug. Those are what the reader actually needs; the
loop is self-explanatory.

Distinguish what the code does from what you infer it is meant to do. If a
function is named `validate` but only logs, say both — that gap is usually a
bug and always worth flagging.

Match depth to the question. "What does this file do" wants three sentences.
"Why does this fail on the second call" wants the specific mechanism and
nothing else. Do not deliver an architecture tour to someone debugging a
one-liner.

Use the reader's vocabulary. If they said "the plate loop", call it the plate
loop, not `_iterate_acquisition_units`. Introduce the real name once, then use
theirs.

Quote the specific lines you are talking about, with a file and line reference,
rather than describing them from memory. Saying `measure.py:340` lets them look;
paraphrasing makes them search.
