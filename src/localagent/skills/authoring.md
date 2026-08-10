---
name: Authoring skills & tools
category: Meta
description: How to write a new skill or tool for yourself, and which of the two a request needs.
default: false
---

You can extend yourself. Write a skill file to give yourself durable
instructions, or a tool file to give yourself a capability you do not have.

## Which one does the request need?

A **skill** is instructions. It changes how you behave with capabilities you
already have. Domain conventions, output style, a checklist, a workflow.

A **tool** is code. It gives you an action you cannot otherwise perform:
reaching a service, calling a library, doing a computation you cannot do
reliably in your head.

The test: if the request could be satisfied by a person telling you something,
it is a skill. If it requires the machine to *do* something, it is a tool.
"Always cite sources in APA" is a skill. "Query PubMed" is a tool.

If asked for a "skill to browse the web" or similar, say plainly that the
capability needs a tool, and offer to write that instead.

## Writing a skill

Write to `~/.config/localagent/skills/<key>.md` with `write_file`. It loads on
the next application launch.

```markdown
---
name: Human readable name
category: Core | Coding | Science | Web | Writing | Meta
description: One line, shown in the dropdown.
default: false
---

The instructions themselves, in plain prose.
```

Keep it under ~400 words. Every enabled skill rides in the system prompt on
every request, so length is a recurring cost. Write directives, not
explanations — "Report the n for each group" beats a paragraph about why
sample size matters. Set `default: true` only for something that helps
essentially every request.

## Writing a tool

Write to `~/.config/localagent/tools/<name>.py` with `write_file`. It must
declare four names:

```python
NAME = "tool_name"                      # snake_case, unique
DESCRIPTION = "What it does and WHEN to call it."
PARAMETERS = {                          # JSON Schema
    "type": "object",
    "properties": {"arg": {"type": "string", "description": "..."}},
    "required": ["arg"],
}
REQUIRES_APPROVAL = False               # True if destructive or outbound

def run(workdir: str, arg: str) -> str:
    return "result as a string"
```

Rules that matter:

- **Return a string, never raise.** On failure return `"Error: <what went
  wrong>"` so you can read it and adapt.
- **Set `REQUIRES_APPROVAL = True`** for anything that deletes, overwrites,
  spends money, or sends data outward.
- **Confine file access to `workdir`.** Resolve paths and reject anything that
  escapes it.
- Put the trigger condition in `DESCRIPTION`. That is what determines whether
  you actually reach for it later.

A new tool does **not** load automatically — a human must read it and enable
it. Say so when you write one, and summarise what it does so they can review
it quickly.
