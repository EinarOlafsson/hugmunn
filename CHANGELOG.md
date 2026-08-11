# Changelog

## 0.7.1

Fixes downloaded models reporting themselves as not downloaded.

The downloader wrote every file as `destination / basename`, dropping the
subdirectory in the repo path. `write-big`, `code-q6` and `agentic` therefore
landed beside `gguf/` instead of inside the folder their launch script opens,
so `is_available()` stayed false after a successful download. Only
`uncensored-big` worked, being a single file with no subdirectory.

- Destinations now come from the launch script — `expected_model_path` parses
  its `--model`, and shards are placed beside it as llama.cpp expects. The
  script loads the weights, so it is the authority on where they go.
- Downloading to another disk still works: bytes land there and a symlink is
  created at the expected path, so the script needs no editing.
- With nothing downloaded, the picker now selects the *smallest* model rather
  than index 0. Previously a fresh machine reported the 27B missing while the
  user had a 122B on disk.
- Fixed a latent bug the new tests caught: `Path()` is `PosixPath('.')` and
  therefore truthy, so a missing script produced relative targets and would
  have written weights into the current working directory.

## 0.7.0

In-app model downloads, and the resource meters move to the bottom.

- Models that have not been downloaded are now **selectable** rather than
  greyed out — picking one opens a dialog asking where the weights should go.
  A disabled item cannot be clicked, which made the offer unreachable.
- The dialog evaluates the chosen disk before you commit: free space against
  the model's size, and what kind of device it is, read from
  `/sys/block/*/queue/rotational` rather than guessed from the name. NVMe and
  SSD pass; an HDD or network mount warns and the button becomes "Download
  anyway"; too little space blocks outright.
- The HDD warning is about inference, not just download time. A GGUF is
  memory-mapped, so a large model reads from disk on every turn, not only at
  load.
- Progress bar in the sidebar with byte counts and per-file position.
  Downloads resume from a partial file with a Range request rather than
  restarting — at 87 GB over a link measured between 3 and 29 MB/s, losing
  progress to a dropped connection is expensive.
- Every model now records its repo, file list and size (480 GB across the
  nine). Sharded quants list every shard, and a test asserts the count matches
  the `-of-0000N` suffix, because a partial set loads with an opaque error.

## 0.6.1

Fixes an HTTP 400 on send when many skills were enabled.

Enabling every skill costs ~12,000 tokens and the tool schemas another
~2,300. On a 16K model that is 90% of the context consumed before the first
message, so the first real exchange overflowed and llama-server returned 400 —
surfaced raw as "HTTP error", which said nothing about the cause.

- `ModelSpec.context_tokens` now reads `--ctx-size` from the launch script, so
  the app knows each model's real window.
- The skills panel warns when skills + tool schemas + effort exceed 60% of the
  selected model's context, naming the number and the percentage.
- HTTP failures from the server are explained rather than echoed: a context
  overflow says which setting to change, a 503 says the model is still loading.

## 0.6.0

Live CPU / RAM / GPU / VRAM meters in the sidebar.

Read from `/proc` and `nvidia-smi` directly rather than adding a psutil
dependency. Bars are colour-coded by pressure, and the note under them names
the specific problem rather than saying "high usage" — swap in use, VRAM
exhausted, or too little RAM for the large models. Every parser degrades to
zeros instead of raising, since it runs on a one-second timer.

## 0.5.0

Effort tiers and autonomy levels.

- **Effort** — Quick / Standard / Thorough / Exhaustive, each injecting its own
  instruction block. Tier 4 grants `spawn_agent`.
- **Subagents** — depth 1 only, tools named explicitly and defaulting to none,
  mutating tools ungrantable. The child never sees the parent's reasoning,
  because an agent told the expected answer tends to confirm it.
- **Autonomy** — confirm-all / ask-to-write / workspace / full. System roots and
  `site-packages` stay protected at every level; editable (`pip install -e`)
  sources do not, since their source is not in `site-packages`.

179 tests.

## 0.4.0

Ten tools and nine skills; a latent circular import fixed.

- `edit_file` `glob_files` `diff_files` `python_exec` `sql_schema` `sql_query`
  `pubmed_search` `arxiv_search` `read_pdf` `image_info`
- Skills: Debugging, Writing tests, Refactoring, Explaining code, Planning,
  Literature review, Experimental design, Data wrangling, spacr codebase.
  Later: Reproducibility, Git workflow, Figures & plots, Shell & CLI,
  Performance, File formats — and a `when:` trigger on every skill.
- The tool registry now builds on first attribute access. Previously
  `devtools`/`research` imported helpers from `tools` while `tools` registered
  them at module scope, so importing `research` first raised a
  partially-initialised-module error and importing `tools` first quietly worked.

## 0.3.0

Models can author their own skills and tools.

- User skills in `~/.config/localagent/skills/`, user tools in
  `~/.config/localagent/tools/`.
- Plugins are opt-in: a human reads the file and switches it on. `run_command`
  is already arbitrary execution, so this is not a new capability class — but a
  plugin runs unreviewed after the first approval, where `run_command` is
  approved per invocation.

## 0.2.0

Web access and the skills system.

- `web_search` `web_fetch` `image_search` `download_pdfs`, with an SSRF guard
  that resolves every address a hostname maps to and re-checks after redirects.
- Skills as markdown instruction packs, grouped by category in the sidebar.

## 0.1.0

Initial release: streaming chat, model/server lifecycle, five file and shell
tools, path confinement, approval gating.
