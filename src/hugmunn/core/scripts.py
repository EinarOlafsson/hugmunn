"""Write a model's launch script when there isn't one.

hugmunn could already start a model with no script, by assembling the
command line in memory. That works and it is the wrong shape: the argv exists
only for the duration of the process, so there is nothing to read, nothing to
edit, and nowhere to put a change. ``NCPUMOE`` is the clearest case — moving
expert layers onto the GPU is worth several tok/s on the large MoE models, and
it is a knob with no handle if the command line is invisible.

So the first launch writes the script instead, and from then on the model
starts the same way it would on a machine that has the models repo: through a
file the user owns.

Two rules make that safe to do automatically:

* **Never overwrite.** A script that regenerates itself on every launch would
  discard the tuning it exists to hold, which is worse than not generating one.
* **Same dialect as the repo's own scripts** — a ``BASE`` derived from the
  script's location, a resolved binary, ``NCPUMOE`` overridable from the
  environment. A machine that gains the real repo later then has one kind of
  script, not two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..config import ModelSpec, find_runtime

HEADER = """#!/usr/bin/env bash
# {label}
#
# Written by hugmunn on first launch, because no script existed for this
# model. It is yours now: edit it freely. hugmunn never overwrites an
# existing script, so anything you change here survives.
#
# {blurb}
{ram}#
#   http://127.0.0.1:{port}
set -e

# Derived from this script's own location, so moving or copying the tree keeps
# it working -- and so it does not carry one machine's home directory.
BASE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
NCPUMOE="${{NCPUMOE:-{n_cpu_moe}}}"

LLAMA_SERVER="${{LLAMA_SERVER:-{runtime}}}"
if [ ! -x "$LLAMA_SERVER" ]; then
  LLAMA_SERVER="$(command -v llama-server || true)"
fi
if [ ! -x "$LLAMA_SERVER" ]; then
  echo "llama-server not found; set LLAMA_SERVER=/path/to/llama-server" >&2
  exit 127
fi

exec "$LLAMA_SERVER" \\
  --model "{model}" \\
  --alias {alias} \\
"""

FOOTER_MOE = """
# --n-cpu-moe is how many expert layers stay in system RAM. Lower it to move
# experts onto the GPU and go faster, until you run out of VRAM:
#
#   NCPUMOE=40 ./{script}     # then 30, 20 ... watch nvidia-smi
"""


def _flag_lines(spec: ModelSpec) -> list[str]:
    """The spec's launch flags, one per line, with NCPUMOE as a variable."""
    flags = spec.launch_arguments()
    lines: list[str] = []
    index = 0
    while index < len(flags):
        token = flags[index]
        value = flags[index + 1] if index + 1 < len(flags) else ""
        takes_value = bool(value) and not value.startswith("--")
        if token == "--n-cpu-moe":
            lines.append('  --n-cpu-moe "$NCPUMOE" \\')
        elif takes_value:
            lines.append(f"  {token} {value} \\")
        else:
            lines.append(f"  {token} \\")
        index += 2 if takes_value else 1
    return lines


def render(spec: ModelSpec, runtime: Path) -> str:
    """The full text of ``spec``'s launch script."""
    head = HEADER.format(
        label=spec.label,
        blurb=spec.blurb,
        ram=f"# Needs roughly {spec.ram_gb} GB of free system RAM.\n" if spec.ram_gb else "",
        port=spec.port,
        n_cpu_moe=spec.n_cpu_moe or 999,
        runtime=runtime,
        model=spec.model_path,
        alias=spec.key,
    )
    body = "\n".join(_flag_lines(spec))
    tail = f'\n  --host 127.0.0.1 --port {spec.port} \\\n  "$@"\n'
    if spec.n_cpu_moe:
        tail += FOOTER_MOE.format(script=spec.script)
    return head + body + tail


def write(spec: ModelSpec, report: Callable[[str], None] = lambda _: None) -> Path | None:
    """Generate ``spec``'s launch script. Returns its path, or ``None``.

    ``None`` when there is nothing to write with (no binary, no weights) or
    nowhere to write it. An existing script is returned untouched — see the
    module docstring for why that rule is not negotiable.
    """
    script = spec.script_path
    if script.is_file():
        return script

    runtime = find_runtime()
    if runtime is None:
        report("no llama-server, so no script can be written")
        return None
    if not spec.has_weights():
        report("weights are missing, so no script can be written")
        return None

    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(render(spec, runtime), encoding="utf-8")
        script.chmod(0o755)
    except OSError as exc:
        report(f"could not write a launch script: {exc}")
        return None

    report(f"wrote {script}")
    return script
