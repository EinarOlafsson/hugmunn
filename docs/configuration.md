# Configuration

Settings are stored as JSON and updated through the desktop application.
The Python API accepts its run settings as `Agent` arguments rather than
inheriting the desktop’s current conversation settings.

## Paths and environment variables

| Variable | Default or purpose |
| --- | --- |
| `HUGMUNN_CONFIG_DIR` | `~/.config/hugmunn`; settings, credentials fallback, and sessions |
| `HUGMUNN_MODELS_ROOT` | `~/.claude/models`; registered model files and launch scripts |
| `HUGMUNN_DATA_DIR` | `~/.local/share/hugmunn`; downloaded or built llama.cpp runtime |
| `LLAMA_SERVER` | Path to an existing llama-server executable |
| `HUGMUNN_PYTHON` | External Python executable for snippets in frozen desktop builds |
| `ANTHROPIC_API_KEY` | Anthropic key; overrides a stored key |
| `OPENAI_API_KEY` | OpenAI key; overrides a stored key |

These defaults are currently used on all operating systems. If the old
`~/.config/localagent` directory exists and `~/.config/hugmunn` does not, the
application uses the old directory. Legacy `LOCALAGENT_CONFIG_DIR` and
`LOCALAGENT_MODELS_ROOT` variables remain fallbacks.

Set path overrides before launching the application or importing the library.
Use absolute paths in environment variables. A runtime selected in Settings
takes precedence over automatic discovery; otherwise Hugmunn checks the
environment, managed runtime, PATH, and known local build locations.

## Credentials

For system keyring support, install:

```bash
python -m pip install ".[keyring]"
```

When a working keyring backend is unavailable, Hugmunn writes
`credentials.json` in the configuration directory and requests owner-only file
permissions. That file is not encrypted. On Windows, access also depends on the
user profile’s filesystem permissions. Keys are not stored in `settings.json`.
Environment credentials take precedence over both storage backends.

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
