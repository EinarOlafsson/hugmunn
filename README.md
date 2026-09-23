# Hugmunn

Huginn and Muninn are Odin’s two ravens in Norse mythology. Their names mean
“thought” and “memory”: they travel through the world and return to tell Odin
what they have seen. Hugmunn takes its name from the pair.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/EinarOlafsson/hugmunn/main/src/hugmunn/resources/icons/hugmunn-horizontal-white.svg?v=joined-wings">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/EinarOlafsson/hugmunn/main/src/hugmunn/resources/icons/hugmunn-horizontal-black.svg?v=joined-wings">
  <img src="https://raw.githubusercontent.com/EinarOlafsson/hugmunn/main/src/hugmunn/resources/icons/hugmunn-horizontal-black.svg?v=joined-wings" alt="Hugmunn — two ravens with joined wings sheltering an all-seeing eye" width="420">
</picture>

[![PyPI](https://img.shields.io/pypi/v/hugmunn)](https://pypi.org/project/hugmunn/)
[![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.13-3776AB?logo=python&logoColor=white)](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/installation.md)
[![Tests](https://github.com/EinarOlafsson/hugmunn/actions/workflows/ci.yml/badge.svg?branch=nightly)](https://github.com/EinarOlafsson/hugmunn/actions/workflows/ci.yml)
[![GUI](https://img.shields.io/badge/GUI-Qt%20%28PyQt6%29-41CD52)](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/desktop.md)
[![Installers](https://img.shields.io/github/v/release/EinarOlafsson/hugmunn?label=Installers)](https://github.com/EinarOlafsson/hugmunn/releases/latest)
[![Platforms](https://img.shields.io/badge/Platforms-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/installers.md)
[![Docs](https://img.shields.io/badge/Docs-Guides%20%26%20API-4A9EFF)](https://github.com/EinarOlafsson/hugmunn/tree/main/docs)
[![Source](https://img.shields.io/badge/GitHub-Source-181717?logo=github)](https://github.com/EinarOlafsson/hugmunn)
[![Issues](https://img.shields.io/github/issues/EinarOlafsson/hugmunn)](https://github.com/EinarOlafsson/hugmunn/issues)
[![License](https://img.shields.io/github/license/EinarOlafsson/hugmunn)](https://github.com/EinarOlafsson/hugmunn/blob/main/LICENSE)

Hugmunn is a desktop application and Python library for working with language
models. It connects to local models through [llama.cpp](https://github.com/ggml-org/llama.cpp)
and to cloud models through the Anthropic and OpenAI APIs.

You can chat, attach instruction packs, let a model read files or run tools,
and return to saved conversations. The desktop application and Python API use
the same model clients and tool policies. Local model weights and llama-server
are separate downloads; they are not included in the Python package or desktop
installers.

## Install

Install from PyPI with Python 3.10 or newer in a separate environment:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install hugmunn
hugmunn
```

Or install directly from GitHub:

```bash
python -m pip install "hugmunn @ git+https://github.com/EinarOlafsson/hugmunn.git"
hugmunn
```

`python -m hugmunn` also launches the application. `hugmunn --version` prints the
installed version without opening a window. For development, install a checkout
with `python -m pip install -e .`.

Download desktop packages from
[GitHub Releases](https://github.com/EinarOlafsson/hugmunn/releases/latest): a
Linux `.deb`, Windows setup `.exe`, or macOS `.dmg`. These bundle Python and Qt.
See the [installer guide](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/installers.md)
for installation, portable bundles, and platform requirements.

## First conversation

1. Launch `hugmunn` and choose a provider in the **Model** tab.
2. For a local model, choose a registry entry and download its weights. If you
   already have them, use **hugmunn → Find my models…**. Use
   **hugmunn → Set up llama-server…** to select an existing runtime or install one.
3. For a cloud model, sign in through **Accounts**, then select a model from
   your account’s list. This requires an API key; a chat subscription alone
   does not provide API access.
4. Enter a message and send it with **Ctrl+Enter**.

Local models need enough RAM, GPU memory, and disk space for their weights and
context. The model picker shows estimated requirements. Start with a model that
fits your machine; cloud models do not need a local GPU.

The **Run** tab selects a working directory, enables tools, and controls
approval behavior. With the default **Ask before changing anything** setting,
read tools run directly and writes or shell commands ask for approval. Higher
autonomy levels permit more actions without asking. This policy is not an
operating-system sandbox.

Cloud requests send prompts and tool results to the selected provider. Local
inference stays on the local server, but web tools and downloads still use the
network when requested.

## Desktop controls

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/EinarOlafsson/hugmunn/main/docs/images/desktop-dark.png?v=joined-wings">
  <img src="https://raw.githubusercontent.com/EinarOlafsson/hugmunn/main/docs/images/desktop-light.png?v=joined-wings" alt="Hugmunn desktop with model controls and raven artwork" width="900">
</picture>

| Control | What it changes |
| --- | --- |
| Model | Provider, model, downloads, and server status |
| Run | Working directory, tools, effort, persistence, and autonomy |
| Context | Reasoning options, context size, and overflow handling |
| Tools | Instruction packs, custom tools, and prompt presets |
| Sessions | Saved conversations and their desktop settings |
| System | System prompt |
| View menu | Light, dark, system, and other colour themes |

**Effort** controls instructions and supported provider reasoning settings.
**Persistence** sets the tool-round budget. **Autonomy** decides which tool calls
need approval. These are separate settings.

Conversations are saved as you work. **Ctrl+L** starts a new one. Type `/help`
for commands, or `/remote` to open the same conversation in a browser through a
local, token-protected page. See the
[desktop guide](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/desktop.md)
for details.

## Python API

Discover the models known to the installation:

```python
import hugmunn

for model in hugmunn.models("local"):
    print(model.key, model.label, model.size_gb, model.downloaded)
```

After downloading a model and setting up llama-server, use its registry key:

```python
import hugmunn

with hugmunn.agent("code-glm") as chat:
    print(chat.ask("Explain Python context managers with an example."))
    chat.save("conversation.json")
```

Stream a response with access to a limited set of read tools:

```python
import hugmunn

with hugmunn.agent(
    "code-glm",
    workdir="/path/to/project",
    tools=["list_directory", "read_file", "search_text"],
) as chat:
    for event in chat.run("Read the README and describe this project."):
        if event.kind == "content":
            print(event.text, end="", flush=True)
        elif event.kind == "error":
            raise hugmunn.HugmunnError(event.text)
```

Tools are disabled by default in the Python API. When enabled, calls requiring
approval raise `ApprovalRequired` unless you supply an `approve` callback.
An agent closes the local server it started when its context exits; an already
running server is left running. Library conversations are saved explicitly with
`save()`.

The [API guide](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/python-api.md)
covers cloud models, approval callbacks, streaming, cancellation, and sessions.
The [reference](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/api.rst)
is generated from the public docstrings. Runnable examples are in
[`examples/`](https://github.com/EinarOlafsson/hugmunn/tree/main/examples).

## Configuration and documentation

Settings and sessions normally live in `~/.config/hugmunn/`; model weights
default to `~/.claude/models/`. Override these with `HUGMUNN_CONFIG_DIR` and
`HUGMUNN_MODELS_ROOT`. Set `LLAMA_SERVER` to use an existing runtime.

| Guide | Contents |
| --- | --- |
| [Installation](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/installation.md) | pip, environments, model setup, troubleshooting |
| [Desktop](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/desktop.md) | Controls, tools, sessions, remote access |
| [Python API](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/python-api.md) | Examples and behavior |
| [Configuration](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/configuration.md) | Paths, credentials, skills, custom tools |
| [Installers](https://github.com/EinarOlafsson/hugmunn/blob/main/docs/installers.md) | Native builds and release preparation |
| [Contributing](https://github.com/EinarOlafsson/hugmunn/blob/main/CONTRIBUTING.md) | Development, tests, documentation builds |

Hugmunn’s themes and packaging take inspiration from
[spacr](https://github.com/EinarOlafsson/spacr), my microscopy analysis project.
The applications are installed separately.

## Development

```bash
python -m pip install -e ".[dev,docs,build]"
QT_QPA_PLATFORM=offscreen python -m pytest
python -m sphinx -W --keep-going -b html docs docs/_build/html
python -m build
python -m twine check dist/*.whl dist/*.tar.gz
```

Tests use fake model clients and offscreen Qt windows; they do not require
model downloads or paid API calls. See [CHANGELOG.md](https://github.com/EinarOlafsson/hugmunn/blob/main/CHANGELOG.md)
for changes.

## License

Hugmunn’s source and raven artwork are distributed under the
[MIT license](https://github.com/EinarOlafsson/hugmunn/blob/main/LICENSE).
Model weights, llama.cpp, Qt, and other dependencies have their own licenses.
