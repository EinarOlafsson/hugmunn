"""The five features, and the UI restructure around them."""

from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest

from hugmunn.core import context as contextkit
from hugmunn.core import results as resultkit
from hugmunn.core import review
from hugmunn.core import tuning


# ------------------------------------------ 1. prompt-cache-aware trimming


def messages(count, size=900):
    return [{"role": "user", "content": f"turn {i} " + "x" * size}
            for i in range(count)]


def test_trimming_goes_well_under_the_limit_not_just_under_it():
    """Every trim invalidates the prompt cache and forces a full reprocess.
    Trimming to exactly fit pays that cost again next turn, and the turn after."""
    budget = contextkit.Budget(limit=6000, preamble=200, reserve_output=500)
    result = contextkit.compress(messages(30), budget, contextkit.Strategy.DROP_OLDEST)
    used = contextkit.total_tokens(result.history)
    assert used <= budget.available * 0.75, "should trim well past merely fitting"


def test_trimming_buys_several_append_only_turns():
    """Which is the entire point: appends reuse the cache, trims do not."""
    budget = contextkit.Budget(limit=6000, preamble=200, reserve_output=500)
    kept = contextkit.compress(messages(30), budget,
                               contextkit.Strategy.DROP_OLDEST).history
    turns = 0
    while budget.fits(kept):
        kept = kept + [{"role": "user", "content": "y" * 900}]
        turns += 1
    assert turns >= 4, f"only {turns} turns before the next reprocess"


def test_the_note_explains_why_it_took_more_than_it_had_to():
    budget = contextkit.Budget(limit=6000, preamble=200, reserve_output=500)
    note = contextkit.compress(messages(30), budget,
                               contextkit.Strategy.DROP_OLDEST).note
    assert "prompt cache" in note


def test_a_gentler_target_keeps_more():
    budget = contextkit.Budget(limit=6000, preamble=200, reserve_output=500)
    hard = contextkit.compress(messages(30), budget,
                               contextkit.Strategy.DROP_OLDEST, cache_target=0.3)
    soft = contextkit.compress(messages(30), budget,
                               contextkit.Strategy.DROP_OLDEST, cache_target=0.9)
    assert len(soft.history) >= len(hard.history)


def test_tool_pairs_still_survive_the_harder_trim():
    """The stricter target must not be an excuse to break the one invariant."""
    history = [
        {"role": "user", "content": "x" * 3000},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "y" * 3000},
        {"role": "user", "content": "now"},
        {"role": "assistant", "content": "ok"},
    ]
    result = contextkit.compress(
        history, contextkit.Budget(limit=1500, reserve_output=200),
        contextkit.Strategy.DROP_OLDEST)
    calls = {c["id"] for m in result.history for c in m.get("tool_calls") or []}
    results = {m["tool_call_id"] for m in result.history if m.get("role") == "tool"}
    assert calls == results


# ------------------------------------------------- 2. the tool-result store


def test_a_small_result_passes_through_untouched():
    """A model that learns tool output is sometimes truncated hedges about
    everything it reads, which costs more than the tokens saved."""
    store = resultkit.ResultStore()
    assert store.digest("read_file", "a.txt", "short") == "short"
    assert len(store) == 0


def test_a_large_result_is_replaced_by_a_digest_with_a_handle():
    store = resultkit.ResultStore()
    content = "\n".join(f"line {i}" for i in range(4000))
    digest = store.digest("read_file", "big.py", content)
    assert len(digest) < len(content) / 5
    assert "r1" in digest
    assert "recall(" in digest


def test_the_digest_keeps_the_head_and_the_tail():
    """The head says what it is; the tail is where errors and totals live."""
    store = resultkit.ResultStore()
    content = "FIRST LINE\n" + "middle\n" * 3000 + "LAST LINE"
    digest = store.digest("run_command", "pytest", content)
    assert "FIRST LINE" in digest and "LAST LINE" in digest


def test_the_full_result_is_kept_not_discarded():
    store = resultkit.ResultStore()
    content = "x" * 50_000
    store.digest("read_file", "a", content)
    assert store.get("r1").content == content


def test_recall_by_search_returns_matches_with_context():
    store = resultkit.ResultStore()
    lines = [f"line {i}" for i in range(500)]
    lines[250] = "def the_function_wanted():"
    store.digest("read_file", "a.py", "\n".join(lines))
    out = resultkit.recall(store, "r1", find="the_function_wanted")
    assert "the_function_wanted" in out
    assert "line 249" in out          # context either side


def test_recall_merges_overlapping_context_windows():
    store = resultkit.ResultStore()
    store.digest("x", "y", "\n".join(["hit"] * 40 + ["other"] * 3000))
    out = resultkit.recall(store, "r1", find="hit")
    assert out.count("hit") <= 60     # not multiplied by the context window


def test_recall_is_bounded_so_it_cannot_undo_the_digesting():
    store = resultkit.ResultStore()
    store.digest("x", "y", "z" * 200_000)
    assert len(resultkit.recall(store, "r1", count=99999)) <= resultkit.MAX_RETURN


def test_recall_by_line_range_says_what_is_left():
    store = resultkit.ResultStore()
    store.digest("x", "y", "\n".join(str(i) for i in range(1000)))
    out = resultkit.recall(store, "r1", start=1, count=10)
    assert "not shown" in out and "start=11" in out


def test_an_unknown_handle_lists_the_known_ones():
    store = resultkit.ResultStore()
    store.digest("x", "y", "q" * 5000)
    assert "r1" in resultkit.recall(store, "r99")


def test_no_match_says_so_rather_than_returning_everything():
    store = resultkit.ResultStore()
    store.digest("x", "y", "q" * 5000)
    assert "No line" in resultkit.recall(store, "r1", find="zzzznothere")


def test_the_agent_registers_recall_and_digests_results():
    from hugmunn.core.agent import Agent

    agent = Agent(client=None, workdir="/tmp", system_prompt="")
    assert any(t.name == "recall" for t in agent.extra_tools)
    assert isinstance(agent.results, resultkit.ResultStore)


# ------------------------------------------------------- 3 & 5. measurement


def test_measurements_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    importlib.reload(tuning)

    store = tuning.Measurements()
    store.put(tuning.Measurement("uncensored", 36.7, 420.0, 0, 24576, "RTX 3090",
                                 time.time()))
    assert tuning.Measurements.load().get("uncensored").tokens_per_second == 36.7


def test_an_unmeasured_model_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HUGMUNN_CONFIG_DIR", str(tmp_path))
    from hugmunn import config

    importlib.reload(config)
    importlib.reload(tuning)
    assert tuning.Measurements().get("nothing") is None


def test_a_measurement_summarises_itself():
    assert "36.7 tok/s" in tuning.Measurement("k", 36.7).summary()
    assert "n-cpu-moe 40" in tuning.Measurement("k", 8.0, n_cpu_moe=40).summary()
    assert tuning.Measurement("k", note="skipped — GPU busy").summary().startswith("skipped")


def test_the_expert_ladder_runs_aggressive_to_safe():
    """Fewer experts on the CPU is always faster when it fits; the failure
    mode is running out of VRAM, not being slower. So stop at the first that
    works."""
    assert tuning.NCPUMOE_LADDER[0] < tuning.NCPUMOE_LADDER[-1]
    assert tuning.NCPUMOE_LADDER[-1] == 999      # the always-safe fallback
    assert list(tuning.NCPUMOE_LADDER) == sorted(tuning.NCPUMOE_LADDER)


def test_tuning_a_dense_model_does_nothing():
    from hugmunn import config

    said = []
    list(tuning.tune_expert_split(config.by_key("write"), None, None, said.append))
    assert any("not a mixture-of-experts" in line for line in said)


def test_neither_measurement_nor_tuning_runs_on_a_busy_gpu(monkeypatch):
    """This machine runs other people's jobs. A sweep that evicts a training
    run is not a feature, and a benchmark against a contended card is a number
    that is wrong and looks right."""
    monkeypatch.setattr(tuning, "gpu_is_busy", lambda: (True, "2 processes"))
    from hugmunn import config

    said = []
    list(tuning.tune_expert_split(config.by_key("code-heavy"), None, None, said.append))
    assert any("not tuning" in line for line in said)


def test_the_server_accepts_the_override_the_sweep_needs():
    import inspect

    from hugmunn.core.server import ServerManager

    assert "extra_args" in inspect.signature(ServerManager.start).parameters


def test_an_out_of_vram_failure_is_named_as_such():
    assert tuning._why_it_failed(RuntimeError("CUDA error: out of memory")) == "out of VRAM"


# ------------------------------------------------------ 4. the diff review


def test_an_edit_is_shown_as_a_diff_not_the_whole_file(tmp_path):
    """Three hundred lines of which four differ is not something anyone
    reads; it is something everyone clicks Allow on."""
    target = tmp_path / "config.py"
    target.write_text("\n".join(f"line {i}" for i in range(300)))
    # "line 5" is a prefix of line 50-59, so match a whole line.
    new = target.read_text().replace("\nline 5\n", "\nline five\n")

    change = review.preview(target, new)
    assert change.added == 1 and change.removed == 1
    assert len(change.diff) < len(new) / 5
    assert "2 added" not in change.headline()
    assert "1 added, 1 removed" in change.headline()


def test_a_new_file_shows_its_content(tmp_path):
    change = review.preview(tmp_path / "new.py", "print('hi')\n")
    assert change.is_new
    assert "Create new.py" in change.headline()
    assert "print" in change.diff


def test_writing_identical_content_is_called_out(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("same")
    change = review.preview(target, "same")
    assert change.is_noop
    assert "no change" in change.headline()


def test_a_binary_file_is_not_diffed_against_nonsense(tmp_path):
    target = tmp_path / "a.bin"
    target.write_bytes(b"\x00\x01\x02" * 100)
    change = review.preview(target, "text")
    assert change.binary
    assert "binary" in change.headline()


def test_an_enormous_diff_is_truncated_with_a_count(tmp_path):
    """A diff nobody scrolls to the end of is not review."""
    target = tmp_path / "big.py"
    target.write_text("\n".join(f"line {i}" for i in range(2000)))
    new = "\n".join(f"CHANGED {i}" if i % 10 == 0 else f"line {i}"
                    for i in range(2000))
    change = review.preview(target, new)
    assert "more hunk" in change.diff


def test_the_html_marks_additions_and_removals_differently(tmp_path):
    from hugmunn.ui import theme

    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n")
    change = review.preview(target, "one\nTWO\nthree\n")
    markup = review.html(change, theme.active())
    assert theme.active()["success"] in markup
    assert theme.active()["error"] in markup


def test_an_unreadable_file_is_reported_not_crashed(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_text("x")
    monkeypatch.setattr(
        Path, "read_text",
        lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("nope")))
    assert review.preview(target, "y").binary
