# Changelog

## 0.13.0

Three researched uncensored models, and a way to measure rather than guess.

Model cards are not comparable — one build's "abliterated" is another's
"ultra-uncensored", and neither figure came from the same test. So the new
entries were chosen from download counts, update recency and published method
quality, and `tools/refusal_probe.py` exists so the claim can be checked in
two minutes instead of after a 50 GB download.

| key | model | size | fits |
|---|---|---|---|
| `uncensored-gemma` | Gemma-4-31B, Heretic | 21.8 GB | entirely on a 24 GB card |
| `uncensored-fast` | Gemma-4-26B-A4B, MoE, 4B active | 22.6 GB | entirely on a 24 GB card |
| `uncensored-code` | Qwen3-Coder-Next 80B abliterated | 48.6 GB | the slot the stock 80B already uses |

Gemma-based abliterations preserve the most capability of any family measured
(MMLU 68.0 against 68.4 aligned), and Heretic co-minimises refusals *against*
KL divergence from the original rather than trading one for the other — 3/100
refusals at 0.16 KL, where manual abliteration of the same model managed
0.45–1.04. Every URL and file size in the table was verified against the
Hugging Face API rather than transcribed from a page.

`tools/refusal_probe.py` asks a running server twelve ordinary graduate
parasitology and pharmacology questions — mechanism, toxicity, dosing, immune
evasion, containment — and reports how many were answered, hedged, or refused.
It judges refusal on the *opening*, because a model that answers in full and
then adds a disclaimer has complied, and counting that as a refusal makes
every model look censored.

## 0.12.1

Fixes a crash on every send, introduced in 0.12.0.

`self._thinking` was already the live ThinkingCard widget. Adding a method of
the same name did not replace it — an instance attribute shadows a method — so
`self._thinking()` resolved to `None` and every send aborted the process with
`'NoneType' object is not callable`. Renamed to `_thinking_enabled`.

It shipped because every UI test built the window and read its controls, and
none of them pressed Send. A window whose controls all report correctly and
whose Send button crashes is not a tested window. There is now a test that runs
a full turn against a stub client — verified by reintroducing the bug and
watching eight of nine fail — plus a check that no method in the file shares a
name with an instance attribute, since Python gives no warning for that and the
failure only appears when the method is called.

## 0.12.0

Reasoning is a control, and the system prompt is a choice.

Both came out of one finding: a model that refused with thinking on and
complied with it off. The mechanism is in the chat template — Qwen3 defines
`enable_thinking` and **defaults it to true**, so a model left alone thinks
before every answer, and the refusal forms during the thinking.

- **Reasoning** is a sidebar control, per model, remembered. Sent as a
  per-request template argument rather than only a launch flag, so changing it
  takes effect on the next message with no reload of the weights. The
  uncensored models default to off, and their descriptions now say why.
- **System prompt presets**: Assistant, Minimal, None, Coding, Writing,
  Research. This is the largest lever the app has over behaviour and it
  previously had exactly one setting, written once and never chosen. Minimal
  and None matter on the abliterated builds — assistant framing re-establishes
  the role that refusal behaviour belongs to, and those two stop adding a
  persona nobody asked for. Research states that clinical and biological
  material is the normal subject matter rather than a warning sign.

## 0.11.0

Context controls, context compression, and generated launch scripts.

### Context

- **Context size** is a control in the sidebar, per model, remembered. Applied
  at launch, because llama.cpp allocates the KV cache once when the model
  loads — so changing it says it needs a restart rather than appearing to work.
  On a script launch it is appended as a second `--ctx-size`, the same
  last-wins mechanism the weight-path override uses, so the script keeps its
  tuning and is never rewritten.
- **A usage meter**, counting the system prompt, enabled skills and tool
  schemas — not just the conversation. That preamble is re-sent on every
  request and with every skill enabled it has exceeded a 16K window on its
  own, which used to surface as an unexplained HTTP 400 before a word was
  typed.
- **Context handling** when a conversation stops fitting: keep everything and
  refuse, drop the oldest turns, or replace them with a summary the model
  writes. What was dropped is said in the transcript rather than silently
  happening.

The part that is not a preference is *what may be dropped together*. An
assistant turn that called a tool owns the result that follows it; separating
them leaves the model looking at a request it never got an answer to, and
Anthropic rejects it outright — every `tool_use` needs a matching
`tool_result`. History is compressed in whole turns, and a test asserts the
two never come apart.

### Launch scripts are generated on first run

0.9.1 could start a model with no script by assembling the command line in
memory. That worked and was the wrong shape: an argv lasts as long as the
process, so there is nothing to read and nowhere to put a change. `NCPUMOE` is
the clearest case — worth several tok/s on the large MoE models, and a knob
with no handle if the command line is invisible.

The first launch now writes the script instead, in the same dialect as the
models repo's own: a relocatable `BASE`, a resolved binary, `NCPUMOE` as an
environment-overridable variable with a comment explaining how to tune it. It
is **never overwritten** — a script exists to hold tuning, and one that
regenerates itself would discard the thing it is for.

### Fixed

- The overlap test compared vertical extents rather than rectangles, so two
  controls side by side in a row read as a collision. It now intersects real
  geometry.
- Tests that reload `config` against a temporary models root no longer leak
  it into the next test — `monkeypatch` restores the environment variable but
  not the module that already read it, which cost two failures and sixteen
  silent skips.

## 0.10.2

"All 49 layers on GPU" and "extremely slow" were not a contradiction.

The placement was right and the model was still unusable, because the
direct-launch fallback added in 0.9.1 sent only enough flags to load the
weights. It called that being conservative. It was not — the omitted flags are
the ones that decide whether a model is usable:

- **No `--reasoning off`.** Qwen3.6 thinks by default, so the server spent
  hundreds of tokens on a chain of thought before the first visible word. At
  37 tok/s that is most of a minute of silence, which is indistinguishable
  from a slow model. This is the likely cause of the report.
- **No `--n-cpu-moe`.** A 122B mixture-of-experts had nowhere to put its
  experts but a 24 GB card.
- **No KV quantization.** At 16–32K context an f16 cache is gigabytes of VRAM
  the model needed for itself.
- **The wrong context size** — a flat 16K, where five of the nine models ask
  for 32K.

The tuning is data on `ModelSpec` now, so the fallback and the script build
from the same source. A test compares every field against the shipped script
and fails on any divergence; it caught the context sizes immediately.

## 0.10.1

Three reported problems, one of them mine twice over.

### Text drew a box of the wrong colour

`QWidget { background: … }` in the stylesheet painted *every* widget, labels
included, so a label inside a raised panel drew a window-coloured rectangle
behind its text — every piece of text in the app carrying a box that did not
match its container. Barely visible on the dark theme, where the window and the
panels are two near-blacks a few levels apart; obvious on the other three.

Containers are painted explicitly now and everything else inherits what it sits
on. Caught by rendering the widgets offscreen and sampling pixels, since the
whole symptom is what it looks like: the tests take the dominant colour of each
label's rectangle and compare it against the palette, across all four themes.

### The resource meters overlapped their neighbours

The sidebar is fifteen sections tall and wants about 1400px. On any normal
screen there is a shortfall, and Qt resolves that by compressing children below
their size hints — but a fixed-height widget cannot compress, so it gets drawn
*outside* its parent, on top of whatever is next to it. The four meters are
30px each in a frame the layout had squeezed to 65px.

The sidebar scrolls now, which is the only arrangement where nothing overlaps at
any window size, and the meters claim their full height so no layout tries. A
test walks the sidebar's geometry at four window heights looking for
intersecting rectangles.

### "It works but it is extremely slow"

The app could not answer whether a model was on the GPU, which is the first
thing worth knowing — and localagent may itself have built a CPU-only binary,
if the CUDA toolkit was absent at build time.

- The server status now reads **"all 49 layers on GPU"**, or **"0 layers on
  GPU — running entirely on the CPU"**, parsed from llama.cpp's own startup log.
- A model that loads with nothing on the GPU, on a machine that *has* one, now
  says so and gives the fix.
- The runtime dialog reports whether a binary is GPU-capable via
  `--list-devices`, rather than showing a version string. `--version` names the
  compiler and says nothing about the backend, so "it built fine" was never
  evidence the card was in play.

## 0.10.0

localagent sets up llama.cpp itself, on any machine.

0.9.3 could tell you a binary was missing and show you the commands. That is
still two manual steps, and the second one — running a script inside the models
repo — is circular on the machine that needs it most, because that repo is
often not cloned there.

- **It builds llama.cpp for you.** Clone, configure, compile, install. The GPU's
  compute capability is read from `nvidia-smi` and the build targets only that
  card; building for every architecture turns four minutes into most of an
  hour. Verified end to end here: 225 seconds with CUDA on an RTX 3090, and the
  resulting binary reports the card correctly.
- **Offered on launch** when that is genuinely the blocker — weights present,
  no binary — rather than leaving a model list where nothing starts and no
  indication why. Not offered when nothing is downloaded yet, because then the
  binary is not the problem.
- **Compiler output streams into the dialog.** A build with no output for four
  minutes is indistinguishable from a hang, and gets killed.
- **Preflight before anything is promised**: it says what it will do, what it
  will cost, and — if cmake or a compiler is missing — the install command for
  this machine's package manager. A GPU present without `nvcc` is called out
  specifically, since silently producing a CPU build on a machine with a 3090
  wastes the thing the user bought.
- **Download a prebuilt** for machines with no toolchain, labelled CPU-only on
  Linux because llama.cpp publishes CUDA builds for Windows and not for Linux.
  The confirmation says what that costs rather than presenting it as equivalent.
- Installs to `~/.local/share/localagent/`, so none of it depends on the models
  repo existing. Capped at 16 cores — this machine runs other people's jobs.

## 0.9.3

Makes "no llama-server on this machine" fixable from inside the app.

Weights copy between machines fine — 87 GB is only slow. The binary does not:
it is compiled against one machine's CPU features and CUDA version, which is
why it is gitignored inside the models repo. So a second machine reliably ends
up with correct weights and nothing to run them with, and *"build llama.cpp or
set LLAMA_SERVER"* is accurate without being any help.

- **localagent → Set up llama-server…** searches the conventional locations,
  reports what each build says about itself (`--version` tells you whether it
  has CUDA), lets you browse to one, and otherwise gives the exact build
  commands — with the CUDA architecture filled in for the GPU present.
- Pressing **Start server** in that state now offers the dialog instead of
  restating the problem.
- A chosen binary is **remembered**, so it no longer has to be exported into
  the environment before launching the GUI. Priority: what you chose, then
  `$LLAMA_SERVER`, then `bin/`, then PATH, then conventional locations.
- A chosen path that has since been deleted no longer shadows a working one.
- `*.gguf` is gitignored. An 87 GB weight file landed in the working tree on
  another machine — the download dialog takes any folder, and the repo is a
  reasonable-looking choice — and `git add -A` would have tried to stage it.

The models repo gains `scripts/setup-llama.sh`, which clones and builds
llama.cpp for the card actually present. Reading the compute capability from
`nvidia-smi` rather than building for every architecture is the difference
between a six-minute build and the better part of an hour, and it
distinguishes "no GPU" from "GPU but no nvcc" — the second is a missing CUDA
toolkit and worth fixing rather than silently falling back to CPU.

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
