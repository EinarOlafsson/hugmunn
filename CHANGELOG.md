# Changelog

## 0.9.2

Fixes the "1 sibling is not beside it" message, which was wrong twice over.

The download dialog opens for whichever model is *selected*, and on a fresh
install that is the smallest one — Qwen3.6-27B coding, 17.6 GB. Pointing that
dialog at a different model's weights was reported as a missing shard, naming
a file from an unrelated model, about a model that ships as a single file.
Neither half was true and there was nothing the user could do with it.

- **Weight files are identified by name.** Point the dialog at any `.gguf` and
  it says which model that is, and offers to record it there. A file matching
  nothing says so, rather than inventing a shard problem.
- **Scan a folder…** in the dialog, and **Find my models…** in the menu: point
  at a directory and every model in it is recorded at once. This is the common
  case — weights arrive on a second machine as a copied folder — and having to
  open an offer-to-download flow for the wrong model to say "they are over
  here" is what made it confusing.
- The scan looks three levels down, which covers the layout the download
  scripts produce, and is bounded rather than a full `rglob`: these folders sit
  on network mounts holding hundreds of gigabytes.
- An incomplete shard set is not reported as found — two of three parts loads
  with an opaque llama.cpp error.
- The real missing-shard message now names the model it belongs to.

A test asserts no two registered models claim the same filename, since
identification is by name and a collision would silently misfile.

## 0.9.1

Fixes a machine with correct weights reporting them as not downloaded.

Three things must line up before a local model runs, and they arrive
independently: the **weights** (copied by hand, or downloaded), the **launch
scripts** (a separate repo) and a **llama-server binary** (gitignored inside
that repo). They were collapsed into one boolean, so any one of them missing
produced the same message — *not downloaded* — and an offer to re-fetch 87 GB
that was already on disk.

- `ModelSpec.readiness()` reports the three separately and names the fix for
  whichever is missing. `has_weights()` is what "downloaded" means; being
  launchable is a different question.
- A download is only offered when the weights are genuinely absent.
- **A model can now start without its launch script**, as long as the weights
  and a binary are there: `llama-server` is invoked directly with `--fit on`
  and a conservative set of flags. The scripts carry tuning worth keeping —
  `--n-cpu-moe` splits, sampling, reasoning mode — so this says it is running
  without them rather than pretending to be equivalent.
- `find_runtime()` looks at `$LLAMA_SERVER`, then `bin/llama-server`, then
  PATH.

The root cause was in the models repo: every launch script opened with a
literal `BASE=/home/olafsson/.claude/models`, so on any other machine or user
account it resolved to nothing. `BASE` now comes from the script's own
location, and the binary is resolved rather than assumed.

## 0.9.0

Claude and ChatGPT alongside the local models, and spaCR's themes.

### Cloud providers

- The model picker is now two levels: **provider** (Local models / Claude /
  ChatGPT) and then the model within it. Three lists chosen on entirely
  different grounds — VRAM on one side, price and capability on the other —
  do not belong flattened into one dropdown of thirty entries.
- **Sign-in happens in the app.** Selecting Claude or ChatGPT while signed
  out opens the dialog, which verifies the key by listing what it can reach
  before saving it. A key that is stored and wrong fails later, mid
  conversation, as an opaque 401.
- **Every model the account can reach**, not a shipped list. The catalogue is
  fetched from the provider on sign-in, so a model released after this was
  written appears and one the account cannot use does not. The built-in list
  is a fallback that exists so the dropdown is not empty.
- API keys go to the system keyring when there is one, and to a mode-600 file
  when there is not — created 0600 rather than chmod'ed after, so there is no
  window where it is world-readable. Never to `settings.json`, which is the
  file a user copies between machines. `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
  override stored state.
- A cloud model is marked as one, in the sidebar, every time. That the
  conversation leaves the machine is the one property of a model choice that
  is invisible in the answer.

The OpenAI adapter is thin — its Chat Completions API is the shape llama.cpp
imitates. Anthropic's is a genuine translation: `/v1/messages`, `x-api-key`,
the system prompt as a top-level field, `input_schema` rather than
`parameters`, `tool_use` blocks rather than `tool_calls`, and no `tool` role
at all. Results ride back as `tool_result` blocks inside a *user* message, and
every result for one assistant turn must arrive together — so consecutive
results are merged. Sending them separately works for a single call and fails
the moment a model requests two tools at once.

### Effort and autonomy on cloud models

- **Effort** was prompt-only, because a local model has no dial. Claude and
  GPT do, so the tier now sets one: an extended-thinking budget (0 / 4K / 16K
  / 32K tokens) on Anthropic, `reasoning_effort` (minimal / low / medium /
  high) on OpenAI. The prompt text is still sent — the budget decides how long
  the model may think, the instructions decide what to do with the time.
  A budget larger than the model's output ceiling is clamped rather than sent,
  leaving room to answer; spending the whole allowance on thinking ends the
  turn mid-thought and shows nothing.
- **Autonomy** needs no mapping and the sidebar now says why: tools execute on
  *this* machine whichever model asked, so the policy is unchanged and no API
  can widen it. What does change is the consequence of a read — a file a cloud
  model reads is a file that provider receives — and the level-1 and level-4
  blurbs now say so.

### Themes, from spaCR

- Four palettes — **Dark**, **Light**, **Glass**, **Cell** — plus *Match the
  system*. Switchable live from the View menu or Settings.
- Every palette is checked against WCAG AA on every surface a colour can
  appear on, and a failure is a test failure rather than a matter of taste.
- `ui/style.py` is now a live proxy rather than a set of constants. spaCR
  shipped a module-level `PALETTE` holding the dark colours, two dozen widgets
  imported it, and the light theme drew near-black panels on a near-white page
  — measured at 1.08:1. Three captures of that kind existed here and are
  fixed: a class-level colour dict, a default argument, and rendered markdown
  that carries its CSS inline and has to be re-rendered rather than repainted.

### Release resources

Adapted from spaCR's `resource_cleanup`, including its refusal: no process is
killed, nothing needs root, no `drop_caches`. Reachable from the localagent
menu and from Settings, each behind a confirmation that names what will happen
rather than asking "are you sure?".

The VRAM button differs from spaCR's and the difference is the honest part.
spaCR holds VRAM through torch in its own process, so clearing it is
`empty_cache()`. localagent holds none — llama-server does, in a child
process, and 20 GB of weights is the whole of it. So the button stops the
server, says that unloading is all-or-nothing, and leaves an *adopted* server
alone: somebody else started it, and stopping it is not this button's call.

### Fixed

- Cancelling the sign-in dialog reopened it without limit. `_sign_in`
  refreshes the model list on close, and the refresh is what offers the
  dialog, so a cancelled sign-in re-entered the same path and the modal could
  not be escaped.

## 0.8.0

Weights can live anywhere; the app remembers where.

- `Settings.model_paths` stores an absolute path per model. Availability
  checks, shard verification and launching all follow it.
- The override is applied by passing `--model` as an extra argument to the
  launch script — the scripts forward `"$@"` and llama.cpp takes the last
  occurrence — so the script keeps its per-model tuning and is never rewritten.
  This replaces the 0.7.1 symlink, which worked but was indirect.
- **"I already have it…"** in the download dialog points the app at weights
  copied from another machine or fetched outside it. Selecting one shard of a
  set is rejected with the specific siblings that are missing, rather than
  failing later inside llama.cpp.
- After a download the destination is recorded automatically, so a chosen disk
  survives a restart.

Also fixes three launch scripts (`write.sh`, `code.sh`, `code-heavy.sh`) that
were mode 644 on disk and in git and therefore could never be started. The
server now repairs a missing `+x` rather than refusing.

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
