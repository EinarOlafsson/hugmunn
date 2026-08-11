"""System prompt presets.

The system prompt is the largest lever any app has over how a local model
behaves, and localagent shipped exactly one — assistant framing that was never
chosen deliberately, just written once and left. It is worth making that a
choice, because the right prompt for driving tools is not the right prompt for
drafting prose, and neither is right for a model you are evaluating and want
to see unshaped.

On the abliterated builds specifically: assistant framing is part of what
brings refusal behaviour back. Abliteration edits out refusal *directions*, but
a prompt that casts the model as a careful assistant re-establishes the role
those directions belonged to. The minimal preset exists for that — it is not a
jailbreak, it simply stops adding a persona the user did not ask for.

What no preset can do is make a model that never refuses. Alignment is spread
through the weights and the training data, not held in one place a prompt can
reach around, so residual refusals on a strongly-trained topic survive any
framing. When that happens the answer is a different model, not a longer prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT = (
    "You are a capable coding and writing assistant running locally on the "
    "user's machine. Be direct and concise. When you use a tool, use its "
    "result rather than guessing. Prefer showing code over describing it."
)

MINIMAL = (
    "Answer the user's question directly and completely. Do not add caveats, "
    "disclaimers, or offers of further help unless they were asked for."
)

NONE = ""

CODING = (
    "You are working on code on the user's machine. Read a file before "
    "editing it. Run the tests after changing anything. Report what the "
    "output actually said rather than what you expected. Show code rather "
    "than describing it, and say plainly when something did not work."
)

WRITING = (
    "You are drafting prose. Match the register the user is writing in. "
    "Prefer concrete detail to summary, and specific words to general ones. "
    "Do not open by restating the request or close by offering to revise."
)

RESEARCH = (
    "You are helping with technical and scientific research. Cite what you "
    "are drawing on, and mark clearly where you are inferring rather than "
    "recalling. Scientific and clinical subject matter is the normal "
    "material here — discuss mechanisms, methods, dosages, pathogens and "
    "failure modes as a domain textbook would, without hedging or refusing "
    "on the grounds that a topic sounds sensitive. Say plainly when you do "
    "not know."
)


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    text: str
    note: str


PRESETS: tuple[Preset, ...] = (
    Preset("default", "Assistant (default)", DEFAULT,
           "General purpose. Adds assistant framing, which on abliterated "
           "models contributes to refusals."),
    Preset("minimal", "Minimal", MINIMAL,
           "Asks for a direct answer and nothing else. No persona, no role."),
    Preset("none", "None", NONE,
           "No system prompt at all. The model behaves as its base training "
           "and chat template dictate — the least shaped it can be."),
    Preset("coding", "Coding", CODING,
           "Read before editing, run the tests, report real output."),
    Preset("writing", "Writing", WRITING,
           "Match the register, prefer concrete detail, skip the preamble."),
    Preset("research", "Research", RESEARCH,
           "For technical and scientific work, where clinical and biological "
           "subject matter is the normal material rather than a red flag."),
)

BY_KEY = {preset.key: preset for preset in PRESETS}


def match(text: str) -> str:
    """Which preset this text is, or ``"custom"`` if the user edited it."""
    for preset in PRESETS:
        if text.strip() == preset.text.strip():
            return preset.key
    return "custom"
