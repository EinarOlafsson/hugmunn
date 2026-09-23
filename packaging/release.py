"""Plan a versioned release and extract its notes without importing the app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def version() -> str:
    """Read and validate the release version from its single source."""
    value = runpy.run_path(str(ROOT / "src/hugmunn/_version.py"))["__version__"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
        raise ValueError(f"Expected a three- or four-part numeric version, got {value!r}")
    return value


def notes(value: str) -> str:
    """Return the current version's changelog section, failing if it is absent."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(value)}(?:[ \t]+[^\n]*)?\n(.*?)(?=^## |\Z)",
                      changelog, re.M | re.S)
    if match is None:
        raise ValueError(f"Add a '## {value}' section to CHANGELOG.md before releasing")
    return match.group(1).strip() + "\n"


def on_pypi(value: str) -> bool:
    """Check whether this exact version exists; propagate service failures."""
    try:
        with urlopen(f"https://pypi.org/pypi/hugmunn/{value}/json", timeout=30):
            return True
    except HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def plan() -> None:
    """Release new versions; resume interrupted releases only at the same commit.

    Skip versions available on both GitHub and PyPI. Creating the tag before
    either publication ties retries to the tested source commit.
    """
    value = version()
    tag = f"v{value}"
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != f"refs/tags/{tag}":
        raise ValueError(f"Selected tag does not match package version {value}")
    repository = os.environ["GITHUB_REPOSITORY"]
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode and "404" not in result.stderr:
        raise RuntimeError(f"Could not check GitHub release: {result.stderr.strip()}")
    published = result.returncode == 0 and not json.loads(result.stdout).get("draft", False)
    needed = not (published and on_pypi(value))
    if needed:
        notes(value)
        existing = subprocess.run(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
                                  capture_output=True, text=True, cwd=ROOT)
        if existing.returncode == 0 and existing.stdout.strip() != os.environ["GITHUB_SHA"]:
            raise ValueError(f"{tag} already names another commit; bump the version before releasing")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"version={value}\nneeded={str(needed).lower()}\n")
    print(f"{tag}: {'build and publish' if needed else 'already released; no upload needed'}")


def main() -> None:
    """Print the version, plan a GitHub Actions release, or write release notes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("version", "plan", "notes"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "plan":
        plan()
    elif args.command == "version":
        print(version())
    else:
        text = notes(version())
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")


if __name__ == "__main__":
    main()
