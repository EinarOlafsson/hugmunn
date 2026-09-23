# Python API guide

Import public names from `hugmunn`. The modules under `hugmunn.core` and
`hugmunn.ui` are implementation details. The [reference](api.rst) is generated
from the public classes and functions.

## Discover models

```python
import hugmunn

for model in hugmunn.models():
    print(model.key, model.provider, model.downloaded)

ready = hugmunn.available_models()
```

`models()` lists registered local models and the in-memory cloud catalogue, or
a built-in fallback if no catalogue has been fetched. It does not make network
requests. `available_models()` checks local weights and launch prerequisites,
or the presence of a cloud key. It does not test available memory, credentials
against the provider, or API quota.

`Model.downloaded` means weights exist for a local model; for a cloud model it
means credentials are present. `Model.context` is a token count and
`Model.size_gb` is the estimated local download size. Registry keys, rather than
filenames or display labels, select models.

## Ask and stream

Set up a local runtime and download weights before constructing the agent.
Construction may take time because it waits for a local server to load.

```python
import hugmunn

with hugmunn.Agent("code-glm", start_server=True) as chat:
    answer = chat.ask("What is a context manager?")
    print(answer)
    print(chat.ask("Show a short example."))
```

`hugmunn.agent(...)` is a context-manager convenience for `Agent(...)`. Both
forms close servers they start. Set `start_server=False` to require a server
already listening at the selected registry entry’s address.

For streaming:

```python
with hugmunn.agent("code-glm") as chat:
    for event in chat.run("Explain this error message: KeyError: 'name'"):
        if event.kind == "content":
            print(event.text, end="", flush=True)
        elif event.kind == "tool_start":
            print(f"\n{event.tool}: {event.summary}")
        elif event.kind == "error":
            raise hugmunn.HugmunnError(event.text)
```

| Event kind | Meaning |
| --- | --- |
| `content` | A fragment of the answer; concatenate in order |
| `reasoning` | A reasoning fragment, when provided separately by the model |
| `tool_start` | Tool name, ID, and summary before execution |
| `tool_result` | Tool output in `text` |
| `denied` | A tool was declined by the approval callback |
| `notice` | Informational run status |
| `error` | A model or run failure described in `text` |
| `done` | End of a turn; may include timing information or cancellation text |

Consume `run()` fully to finish recording the turn. It yields model failures as
`error` events, while `ask()` converts them into `HugmunnError`. Exceptions such
as missing approval or a closed agent can also propagate directly. An agent
retains conversation history between calls and is not safe for concurrent runs.

## Cloud models

Read a key from the environment and discover current model IDs through the
provider instead of copying an ID from an old example:

```python
import os
import hugmunn

hugmunn.sign_in("claude", os.environ["ANTHROPIC_API_KEY"])
for model in hugmunn.models("claude"):
    print(model.key, model.label)
```

Pass a printed key, including its `claude:` prefix, to `hugmunn.agent`. For
OpenAI, use `sign_in("chatgpt", os.environ["OPENAI_API_KEY"])` and
`models("chatgpt")`. `anthropic` and `openai` are also accepted by credential
functions; the discovery filters and public model keys use `claude` and
`chatgpt`.

`sign_in()` makes a blocking request, stores a validated key, and refreshes the
in-memory catalogue. `signed_in()` only checks for a key. Environment keys can
be used without storing them, but call `sign_in()` if you need to discover model
IDs beyond the built-in fallback. Cloud calls can incur provider charges.

## Tools and approval

Tools are disabled unless `tools=True` or a list of allowed tool names is
provided. List the available names with `hugmunn.tool_names()` and skill packs
with `hugmunn.skills()`.

```python
import hugmunn


def approve(name, summary, arguments):
    print(f"Requested tool: {name}\n{summary}\nArguments: {arguments}")
    return input("Allow this call? [y/N] ").strip().lower() == "y"


with hugmunn.agent(
    "code-glm",
    workdir="/path/to/project",
    tools=["read_file", "write_file", "search_text"],
    skills=["python-quality"],
    approve=approve,
    autonomy=hugmunn.Autonomy.ASK_TO_WRITE,
    effort=hugmunn.Effort.THOROUGH,
    persistence=hugmunn.Persistence.NORMAL,
) as chat:
    print(chat.ask("Read README.md and propose a clearer installation section."))
```

The callback runs synchronously when the autonomy policy requires approval.
Returning `True` permits that call; `False` denies it. Without a callback, such a
call raises `ApprovalRequired`. Higher autonomy tiers can permit writes without
calling the callback, so choose the tier as well as the tool allowlist.

The working directory is a tool base path, not a security sandbox. File reads
may access other paths, and shell tools execute with your account’s permissions.
Cloud models receive tool output. Use a separate operating-system environment
if you need stronger isolation.

## Cancellation and sessions

Pass a `threading.Event` as `cancel` to `run()` or `ask()`. Setting the event
requests cancellation; it is checked between streamed chunks and tool rounds,
so it does not immediately terminate a blocking tool call.

```python
import threading
import hugmunn

stop = threading.Event()
with hugmunn.agent("code-glm") as chat:
    for event in chat.run("Describe this project.", cancel=stop):
        if event.kind == "content":
            print(event.text, end="")
    path = chat.save("conversation.json")
    chat.reset()
    restored_messages = chat.load(path)
```

`save()` without a path writes into the configured sessions directory.
`hugmunn.sessions(limit=10)` returns recent nonempty sessions, newest first.
`save(path)` replaces that file atomically and requires its parent directory to
exist. Write failures raise `OSError`. Each save creates a new session ID.

Library saves include messages and model metadata. `load()` restores messages,
not the model, tools, or run settings. Construct the agent with the settings you
want before loading. This differs from desktop session restoration.

## Errors

Catch `hugmunn.HugmunnError` for API-level failures, or its subclasses for a
specific response:

- `ModelNotFound`: unknown model key or credential provider.
- `ApprovalRequired`: a tool needs approval but no callback was supplied.
- `ServerError`: a local server failed to start or become ready.
- `ProviderError`: cloud catalogue lookup or credential validation failed.

Invalid tier values, skill names, and tool names raise `ValueError` before a
server is started. File operations can raise ordinary Python filesystem errors.
