"""Stream a conversation: python examples/chat.py --model code-glm 'Hello'."""

import argparse

import hugmunn


def main() -> None:
    """Send one prompt to a configured model, without tools."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("--model", required=True, help="A key from hugmunn.models()")
    args = parser.parse_args()
    with hugmunn.agent(args.model) as chat:
        for event in chat.run(args.prompt):
            if event.kind == "content":
                print(event.text, end="", flush=True)
            elif event.kind == "error":
                raise hugmunn.HugmunnError(event.text)
    print()


if __name__ == "__main__":
    main()
