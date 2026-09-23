"""Read a project with a model; file-writing and shell tools are not enabled."""

import argparse

import hugmunn


def main() -> None:
    """Summarize project files with an explicit tool allowlist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    with hugmunn.agent(
        args.model,
        workdir=args.directory,
        tools=["list_directory", "read_file", "search_text"],
        effort=hugmunn.Effort.THOROUGH,
    ) as chat:
        print(chat.ask("Read the README and relevant source files. Explain how this project works."))


if __name__ == "__main__":
    main()
