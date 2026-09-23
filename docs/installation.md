# Installation

Hugmunn requires Python 3.10 or newer. Use a dedicated virtual environment,
especially if you also use software based on PySide6: Hugmunn uses PyQt6, and
loading both bindings into one process can crash Qt.

## Install with pip

```bash
git clone https://github.com/EinarOlafsson/hugmunn.git
cd hugmunn
python -m venv .venv
source .venv/bin/activate
python -m pip install .
hugmunn
```

On Windows, activate with `.venv\Scripts\activate` in Command Prompt or
`.\.venv\Scripts\Activate.ps1` in PowerShell. You can also call
`.\.venv\Scripts\python.exe -m pip install .` without activating.

Use `python -m pip install -e .` when developing. To install a built wheel:

```bash
python -m pip install /path/to/hugmunn-0.0.0.4-py3-none-any.whl
```

The wheel includes Python modules, skill packs, and artwork. It does not include
model weights or llama-server. PyQt6 is currently a dependency even for library
use, though importing `hugmunn` does not import Qt or require a display.

The project is prepared for PyPI publication. Until it is published, install
from the checkout, a built wheel, or the GitHub URL in the README.

## Local models

1. Open the **Model** tab and select **Local models**.
2. Choose a registered model and download its weights, or use
   **hugmunn → Find my models…** to locate existing weights.
3. Open **hugmunn → Set up llama-server…**. Select an existing executable,
   download an available prebuilt runtime, or build llama.cpp with the options
   offered for your platform.
4. Start the model and wait for it to finish loading before sending a message.

An existing runtime can also be selected with `LLAMA_SERVER`. A runtime build
needs Git, CMake, and a C++ compiler; CUDA builds also need a compatible CUDA
toolkit. Available prebuilt binaries and GPU backends depend on the platform.
Use the [llama.cpp build instructions](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
for manual builds.

Registry entries carry model-specific filenames, launch arguments, and ports.
The model finder matches known entries; selecting an arbitrary GGUF does not
automatically create a new registry entry. Downloads can be large. Check the
estimated size and available memory before starting one.

The Python API does not display the setup dialogs or automatically download
weights. Configure the runtime and weights before constructing a local agent.

## Cloud models

Use **Accounts → Sign in…** and enter the provider API key. Signing in validates
the key by fetching the model catalogue. Use **Accounts → Refresh model lists**
to update it later. The provider must allow API access, with any required billing
configured; chat subscriptions are separate.

`ANTHROPIC_API_KEY` and `OPENAI_API_KEY` can supply credentials from the
environment. The [configuration guide](configuration.md) describes storage.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `hugmunn` command is missing | Activate the installation environment, or run `python -m hugmunn`. |
| Qt cannot load `xcb` | Install your distribution’s Qt/X11 runtime libraries. On Ubuntu, these commonly include `libxcb-cursor0`, `libxkbcommon-x11-0`, `libegl1`, and `libgl1`. |
| A local model is unavailable | Confirm both weights and a working llama-server or launch script are present. |
| llama-server exits while loading | Check free RAM/VRAM and the runtime log; try a smaller model or context. |
| Cloud authentication fails | Check the provider, key, API billing, and environment overrides. |
| The application crashes after another GUI package is imported | Run Hugmunn in its own environment and avoid mixing PyQt6 with PySide6. |

`QT_QPA_PLATFORM=offscreen` is useful for automated checks, not for normal
interactive use. See [desktop installers](installers.md) for packaged builds.
