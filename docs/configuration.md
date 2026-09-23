# Configuration

Settings are stored as JSON and updated through the desktop application.
The Python API accepts its run settings as `Agent` arguments rather than
inheriting the desktop’s current conversation settings.

## Paths and environment variables

| Variable | Default or purpose |
| --- | --- |
| `HUGMUNN_CONFIG_DIR` | `~/.config/hugmunn`; settings and sessions |
| `HUGMUNN_MODELS_ROOT` | `~/.claude/models`; registered model files and launch scripts |
| `HUGMUNN_DATA_DIR` | `~/.local/share/hugmunn`; downloaded or built llama.cpp runtime |
| `LLAMA_SERVER` | Path to an existing llama-server executable |
| `HUGMUNN_PYTHON` | External Python executable for snippets in frozen desktop builds |
| `CODEX_HOME` | Optional existing Codex configuration/login folder, managed by Codex |
| `CLAUDE_CONFIG_DIR` | Optional existing Claude Code configuration/login folder |

These defaults are currently used on all operating systems. If the old
`~/.config/localagent` directory exists and `~/.config/hugmunn` does not, the
application uses the old directory. Legacy `LOCALAGENT_CONFIG_DIR` and
`LOCALAGENT_MODELS_ROOT` variables remain fallbacks.

Set path overrides before launching the application or importing the library.
Use absolute paths in environment variables. A runtime selected in Settings
takes precedence over automatic discovery; otherwise Hugmunn checks the
environment, managed runtime, PATH, and known local build locations.

## CLI accounts

Claude Code and Codex own their login stores. Hugmunn runs their browser login
and local status commands; it does not read tokens or save new API keys.
API-key environment overrides are excluded from child requests and API-key
login is rejected. Keys saved by earlier Hugmunn releases are left untouched
but are no longer used. Manage or remove those old keys in the original keyring
or `credentials.json` file if desired.

See [CLI accounts](cli-accounts.md) for installation and login commands.

## Skills and custom tools

Built-in skill packs are included in the wheel. Additional Markdown skills are
loaded from `~/.config/hugmunn/skills/`. Python tools are loaded from
`~/.config/hugmunn/tools/`. These two extension paths currently stay under
`~/.config/hugmunn` even when `HUGMUNN_CONFIG_DIR` is overridden.

Use an existing file in `src/hugmunn/skills/` as a skill template. Custom skill
keys can replace built-in keys. The desktop **Tools** tab selects which packs
are attached to a conversation; Python callers pass `skills=[...]` to `Agent`.
Call `hugmunn.skills()` to list the discovered keys.

Custom tool modules run Python code during discovery. Add only code you intend
to execute locally. The extension loader in `hugmunn.core.plugins` is an
internal interface and may change; the public Python API currently exposes the
built-in tool set, not a general plugin registration API.

## Sessions

Desktop sessions are JSON files under `<config>/sessions/`. The desktop keeps a
bounded recent history and records whether a conversation closed normally.
Back up this directory if you need long-term conversation archives.

Python agents keep history in memory until `save()` is called. `save(path)`
writes an explicit JSON file; `save()` creates a file in the sessions directory.
`load(path)` replaces message history while keeping the receiving agent’s model
and settings. See the [API guide](python-api.md).

## Setup preferences

`runtime_backend` is `auto`, `cpu`, `cuda`, `metal` or `vulkan`.
`agreement_version` and `agreement_accepted_at` record desktop agreement
acceptance. `automatic_reports` defaults to true; reports additionally require
acceptance and a verified `github_account`. No GitHub token is stored here.
Use Welcome and setup or Accounts to change these preferences.
