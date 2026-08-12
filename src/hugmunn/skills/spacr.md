---
name: spacr codebase
category: Science
description: Conventions for the spacr high-content imaging package — layout, data model, and rules.
when: working anywhere in the spacr codebase.
default: false
---

spacr is a Python package for high-content imaging analysis: segment cells,
measure them, store the measurements, and analyse the result. Working in it,
these hold.

## Layout and entry points

The package lives under `spacr/`. Two GUIs coexist: the legacy Tkinter one, and
a newer **PySide6** GUI under `spacr/qt/` launched with `spacr-qt`. They are
different codebases — a change to shared logic must keep both working, and the
Tk GUI is updated to fit new code rather than new code being constrained to fit
Tk.

Use `search_text` and `glob_files` to locate a symbol before assuming which
module owns it; responsibilities span `measure`, `plot`, `timelapse`,
`utils`, and `core`.

## Data model

Measurements are written to **SQLite**. Use `sql_schema` before `sql_query` —
table and column names are not guessable, and the schema differs between
object types (cell, nucleus, pathogen, cytoplasm).

Per-object rows are not independent. Objects share a well, and wells share a
plate. Aggregate to the biological replicate before any statistics — a
per-cell n of thousands from three wells is an n of 3.

Images follow instrument naming conventions; channel-to-marker mapping is
metadata, never inferred from channel order.

## Rules that are not negotiable

**No TensorFlow.** TF-backed code paths are removed rather than feature-gated.
If a dependency drags TF in, that is a reason not to use the dependency.

Cellpose is on the **4.x/SAM** API. Calls written against Cellpose 3 break at
runtime; check the installed version's signature rather than assuming.

**Qt threading:** work happens in a worker, results come back through signals,
and a handler that touches widgets must be a bound method of a GUI-thread
object. Connecting `worker.finished` to anything else runs it off the GUI
thread with no error raised — see the Qt desktop apps skill.

## Testing

The repository is under a coverage push; new code is expected to come with
tests, and defensive branches are tested for real rather than excluded. Test
data follows the instrument's naming conventions rather than being synthetic
in shape.
