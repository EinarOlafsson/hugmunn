"""Surviving a crash.

The saving has to happen during the conversation. A clean shutdown is exactly
the case that does not need recovering; the one that does is the process
disappearing, and a save-on-quit never runs then.

So these test the crash path specifically: a session written turn by turn, a
process that never marks it closed, and a next launch that offers it back.
"""

from __future__ import annotations

import importlib
import json
import os
import time

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAGENT_CONFIG_DIR", str(tmp_path))
    from localagent import config

    importlib.reload(config)
    from localagent.core import sessions

    importlib.reload(sessions)
    return sessions, tmp_path


def make(sessions, *, messages=None, closed=False, updated=None, ident=None):
    session = sessions.Session(
        id=ident or sessions.new_id(),
        started=time.time(), updated=updated or time.time(),
        messages=messages if messages is not None else [
            {"role": "user", "content": "what is in config.py"},
            {"role": "assistant", "content": "a model registry"},
        ],
        closed_cleanly=closed,
    )
    sessions.save(session)
    if updated:                       # save() stamps the time itself
        session.updated = updated
        sessions.save(session)
    return session


# ------------------------------------------------------------ round trip


def test_a_conversation_round_trips(store):
    sessions, _ = store
    original = make(sessions)
    loaded = sessions.load(original.path)
    assert loaded is not None
    assert loaded.messages == original.messages
    assert loaded.id == original.id


def test_tool_calls_and_results_survive(store):
    """A restored transcript matches results to calls by id."""
    sessions, _ = store
    history = [
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "text"},
    ]
    loaded = sessions.load(make(sessions, messages=history).path)
    assert loaded.messages == history
    call_ids = {c["id"] for m in loaded.messages for c in m.get("tool_calls") or []}
    result_ids = {m["tool_call_id"] for m in loaded.messages if m["role"] == "tool"}
    assert call_ids == result_ids


def test_the_write_is_atomic(store):
    """A crash mid-write must not truncate the file worth recovering."""
    sessions, tmp_path = store
    session = make(sessions)
    for _ in range(20):
        session.messages.append({"role": "user", "content": "x" * 5000})
        sessions.save(session)
        # Whatever a reader sees at any moment must be valid JSON.
        json.loads(session.path.read_text(encoding="utf-8"))
    assert not list((tmp_path / "sessions").glob("*.tmp"))


def test_an_unreadable_file_is_skipped_not_fatal(store):
    sessions, tmp_path = store
    make(sessions)
    (tmp_path / "sessions" / "broken.json").write_text("{not json", encoding="utf-8")
    assert len(sessions.recent()) == 1


def test_saving_never_raises_when_the_directory_is_unwritable(store, monkeypatch):
    """Losing a transcript is bad; taking the app down with it is worse."""
    sessions, _ = store
    session = make(sessions)
    monkeypatch.setattr(
        sessions.Path, "write_text",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    sessions.save(session)          # must not raise


# ------------------------------------------------------- crash vs clean quit


def test_an_uncleanly_ended_session_is_offered_back(store):
    sessions, _ = store
    crashed = make(sessions, closed=False)
    found = sessions.unfinished()
    assert found is not None and found.id == crashed.id


def test_a_cleanly_closed_session_is_not_offered(store):
    """Offering back what the user deliberately finished trains them to
    dismiss the dialog, which is precisely when it will matter."""
    sessions, _ = store
    make(sessions, closed=True)
    assert sessions.unfinished() is None


def test_mark_closed_persists(store):
    sessions, _ = store
    session = make(sessions, closed=False)
    sessions.mark_closed(session)
    assert sessions.unfinished() is None
    assert sessions.load(session.path).closed_cleanly


def test_a_one_message_session_is_not_worth_offering(store):
    """A single line with no reply is usually a mistype, not lost work."""
    sessions, _ = store
    make(sessions, messages=[{"role": "user", "content": "hm"}], closed=False)
    assert sessions.unfinished() is None


def test_the_newest_crash_wins(store):
    sessions, _ = store
    make(sessions, ident="20260101-000000-1", updated=time.time() - 9000, closed=False)
    newest = make(sessions, ident="20260101-000001-1", updated=time.time(), closed=False)
    assert sessions.unfinished().id == newest.id


# ------------------------------------------------------------- presentation


def test_the_title_is_the_first_thing_the_user_said(store):
    sessions, _ = store
    session = make(sessions, messages=[
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "how does the vacuole form?\nsecond line"},
        {"role": "assistant", "content": "..."},
    ])
    assert session.title == "how does the vacuole form?"


def test_a_summary_marker_is_not_taken_as_the_title(store):
    """A compressed conversation starts with a synthetic user turn."""
    sessions, _ = store
    session = make(sessions, messages=[
        {"role": "user", "content": "[Earlier in this conversation, summarised]\nnotes"},
        {"role": "user", "content": "the real question"},
    ])
    assert session.title == "the real question"


def test_a_long_title_is_trimmed(store):
    sessions, _ = store
    session = make(sessions, messages=[{"role": "user", "content": "x" * 300},
                                       {"role": "assistant", "content": "y"}])
    assert len(session.title) <= 71


def test_turns_counts_user_messages(store):
    sessions, _ = store
    session = make(sessions, messages=[
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"},
    ])
    assert session.turns == 2


@pytest.mark.parametrize("seconds,expected", [
    (10, "just now"), (600, "minutes ago"), (7200, "hours ago"), (200000, "days ago"),
])
def test_the_age_reads_naturally(store, seconds, expected):
    sessions, _ = store
    session = make(sessions)
    session.updated = time.time() - seconds
    assert expected in session.age_phrase()


# ------------------------------------------------------------------ pruning


def test_old_sessions_are_pruned_by_count(store):
    sessions, tmp_path = store
    for i in range(8):
        make(sessions, ident=f"2026010{i}-000000-1")
    sessions.prune(keep=3)
    assert len(list((tmp_path / "sessions").glob("*.json"))) == 3


def test_pruning_keeps_the_newest(store):
    sessions, _ = store
    for i in range(5):
        make(sessions, ident=f"2026010{i}-000000-1")
        time.sleep(0.01)
    newest = sessions.recent()[0].id
    sessions.prune(keep=2)
    assert newest in {s.id for s in sessions.recent()}


def test_stale_temporary_files_are_cleaned_up(store):
    """Left behind by a write the process did not finish."""
    sessions, tmp_path = store
    make(sessions)
    (tmp_path / "sessions" / "orphan.json.tmp").write_text("partial", encoding="utf-8")
    sessions.prune()
    assert not list((tmp_path / "sessions").glob("*.tmp"))


def test_ids_do_not_collide_within_a_second(store, monkeypatch):
    """Two instances started together must not overwrite each other."""
    sessions, _ = store
    now = time.time()
    first = sessions.new_id(now)
    # The real pid is captured before patching: a lambda that calls
    # os.getpid() after replacing it calls itself.
    other = os.getpid() + 1
    monkeypatch.setattr(os, "getpid", lambda: other)
    assert sessions.new_id(now) != first


def test_ids_do_not_collide_within_one_process(store):
    """The case the pid cannot cover, and the one a user actually hits.

    Ctrl+L then typing again inside a second. A collision means the new
    conversation is written over the previous one's file.
    """
    sessions, _ = store
    now = time.time()
    ids = [sessions.new_id(now) for _ in range(50)]
    assert len(set(ids)) == 50


def test_two_conversations_in_the_same_second_are_two_files(store):
    sessions, tmp_path = store
    now = time.time()
    for _ in range(3):
        sessions.save(sessions.Session(
            id=sessions.new_id(now), started=now, updated=now,
            messages=[{"role": "user", "content": "q"},
                      {"role": "assistant", "content": "a"}]))
    assert len(list((tmp_path / "sessions").glob("*.json"))) == 3


def test_recent_is_newest_first(store):
    sessions, _ = store
    for i in range(4):
        make(sessions, ident=f"2026010{i}-000000-1")
        time.sleep(0.01)
    found = sessions.recent()
    assert [s.updated for s in found] == sorted((s.updated for s in found), reverse=True)
