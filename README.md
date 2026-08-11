# localagent

A Qt desktop client for the local LLMs served by llama.cpp — chat, streaming,
and file/shell tool use against models running entirely on your own machine.

Built for the model set under `~/.claude/models` (Qwen3.6, Qwen3.5-122B,
Qwen3-Coder-Next, GLM-4.7-Flash, MiniMax-M2.7), but nothing is specific to those
weights: anything llama.cpp can serve will work.

## Install

```bash
cd /mnt/firecuda2/Claude/repo/localagent
pip install -e .
localagent
```

`pip install -e .` is a normal editable install — edit the source and rerun, no
reinstall needed. Dependencies are PyQt6, httpx, markdown and Pygments.

> Installing into the `spacr` conda environment works and is what the entry
> point currently points at, but a dedicated environment is tidier if you plan
> to change dependency versions.

Run it with `localagent`, `localagent-gui` (no console window), or
`python -m localagent`.

## What it does

**Model and server management.** The sidebar lists every model in the registry
and greys out the ones whose weights haven't finished downloading. Starting one
launches the corresponding script under `~/.claude/models/scripts/` and waits on
`/health`; stopping it tears the process group down. A server already listening
on the target port is adopted rather than duplicated, since two llama-servers on
one GPU would OOM each other.

**Streaming chat** with markdown and syntax-highlighted code blocks. Generation
speed from llama.cpp's own timings is shown under the composer.

**Thinking is separated from the answer.** These models reason by default. When
the server runs with `--reasoning-format deepseek` the chain of thought arrives
as `reasoning_content` and is rendered as a collapsed card, so the visible reply
is just the reply.

**Tools**, so the model can actually work rather than only talk:

| Group | Tools | Approval |
|---|---|---|
| **Files** | `read_file` `list_directory` `search_text` `glob_files` `diff_files` | auto |
| | `write_file` `edit_file` | **asks** |
| **Execution** | `run_command` `python_exec` | **asks** |
| **Web** | `web_search` `web_fetch` `image_search` | auto |
| | `download_pdfs` | **asks** — it writes files |
| **Data** | `sql_schema` `sql_query` | auto (read-only connection) |
| **Research** | `pubmed_search` `arxiv_search` `read_pdf` `image_info` | auto |

Nineteen in total. **`edit_file` is the one that matters most.** Without it the
only way to change a file is `write_file` — reproducing the whole thing, which
is impossible for anything long in a 32K window and silently drops a function
every time it nearly succeeds. It replaces an exact substring and *refuses*
when the target is missing or appears more times than expected, rather than
taking the first match and corrupting the file quietly.

`sql_query` opens the database read-only through a SQLite URI, so a stray
`UPDATE` cannot touch a measurement database. `pubmed_search` and
`arxiv_search` return structured records — real authors, PMIDs, DOIs —
precisely so a citation assembled from one is not invented.

`read_pdf` prefers `pypdf` and falls back to poppler's `pdftotext`;
`image_info` needs Pillow. Both name the missing dependency rather than failing
obscurely — `pip install -e ".[docs]"` gets both.

**Skills** — markdown instruction packs appended to the system prompt, picked
from a category dropdown in the sidebar. Twenty-one ship across Core, Coding,
Science, Web, Writing, and Meta; four are on by default.

A skill is only instructions — it cannot give a model a capability. Web access
is a *tool*, not a skill. What the Web research skill does is tell a model that
already has `web_search` how to use it well: phrase queries like the document
you want, fetch the page rather than answering from a snippet, cite what you
actually read. The **Authoring skills & tools** skill teaches that distinction
explicitly, so a model asked for "a skill to browse the web" pushes back and
offers to write the tool instead.

All 21 together cost roughly 8,750 tokens; the defaults cost 949. On a 16-64K
context even everything-on is affordable, so "Enable all" is a reasonable way
to run. The reason to keep them opt-in is focus rather than budget: a model
given microscopy conventions while writing Python is being pulled in two
directions.

Add your own by dropping a `.md` file into `~/.config/localagent/skills/` with
frontmatter (`name`, `category`, `description`, `default`) — a model can write
one there itself with `write_file`. It is picked up on the next launch, and a
user skill overrides a shipped one with the same filename. A malformed file is
skipped rather than crashing the app.

**Custom tools** live in `~/.config/localagent/tools/` as `.py` files declaring
`NAME`, `DESCRIPTION`, `PARAMETERS`, and `run`. They do not auto-load — enable
each from the sidebar after reading it. That gate is not about capability
(`run_command` is already arbitrary execution) but about review posture:
`run_command` shows you the exact command every time, whereas a loaded plugin
runs unreviewed thereafter. A built-in always wins a name clash, so a plugin
cannot shadow `read_file`.

## Safety model

Two deliberate constraints, both enforced in `core/tools.py` rather than left to
the UI:

**Paths are confined to the working directory.** Every path argument is resolved
to canonical form and rejected if it escapes — `..`, absolute paths, and
symlinks pointing outside all fail before anything is opened.

**Mutating tools always require a human decision.** `write_file` and
`run_command` cannot be auto-approved; the "auto-approve" checkbox only covers
read-only tools. Deny is the default button in the dialog. A local model with
unattended shell access is a bad trade for a little convenience.

**Web tools cannot reach this machine or the local network.** A model-supplied
URL is otherwise an SSRF primitive aimed at the LAN — router admin pages,
internal services, cloud metadata endpoints. `core/web.py` resolves every
address a hostname maps to (a name can map to several; checking only the first
leaves a hole) and refuses loopback, private, link-local, reserved, and
multicast ranges. The check runs again on the final URL after redirects, so a
public address cannot bounce inward. Responses stop at 5 MB read and 15K
characters returned, so a large page cannot swallow the context window.

Tool failures return an error string to the model rather than raising, so a bad
path or a failing command becomes something it can recover from.

## Layout

```
src/localagent/
├── config.py          model registry, shard-completeness checks, settings
├── core/
│   ├── client.py      SSE streaming, tool-call reassembly, timings
│   ├── server.py      llama-server lifecycle, health polling, adoption
│   ├── tools.py       tool schemas, path confinement, executors
│   └── agent.py       the tool-calling loop (no Qt — testable standalone)
└── ui/
    ├── main_window.py sidebar, transcript, composer, approval dialog
    ├── chat.py        message widgets, streaming markdown renderer
    ├── workers.py     QThread wrappers + the approval round-trip
    └── style.py       dark theme
```

The agent loop deliberately has no Qt import, so it can be driven from a script
or a test without a display.

## Two implementation notes

**Approval blocks a worker thread from the GUI thread.** The agent runs off the
GUI thread, but a tool approval must block it until a human answers a dialog
that can only be shown *on* the GUI thread. That round trip is a queued signal
out and a `threading.Event` back (`ui/workers.py`).

**Sharded weights are checked in full.** Big quants ship as
`…-00001-of-00004.gguf` sets. Checking only the first shard reports a
half-downloaded 100 GB model as ready and llama.cpp then fails at load with an
opaque error, so `config._weights_complete()` verifies every shard.

## Development

```bash
pip install -e ".[dev]"
pytest              # 51 tests, no GPU or network needed
```

Tests cover path-traversal rejection, tool-call reassembly across chunk
boundaries, shard completeness, and settings round-trips. The GUI is not
unit-tested; smoke-test it with:

```bash
QT_QPA_PLATFORM=offscreen python -c "
from PyQt6.QtWidgets import QApplication
from localagent.ui.main_window import MainWindow
app = QApplication([]); MainWindow().show(); print('ok')"
```

## Configuration

Settings persist to `~/.config/localagent/settings.json` — selected model,
working directory, tool toggles, and system prompt.

Two environment variables override the defaults:

- `LOCALAGENT_MODELS_ROOT` — where the model scripts and weights live
  (default `~/.claude/models`)
- `LOCALAGENT_CONFIG_DIR` — settings location

To add a model, add a `ModelSpec` to `REGISTRY` in `config.py` pointing at its
launch script and port. Per-model tuning stays in the shell scripts rather than
being duplicated here.

## Keyboard

| | |
|---|---|
| `Enter` | send |
| `Shift+Enter` | newline |
| `Ctrl+L` | new conversation |

## Licence

MIT.
