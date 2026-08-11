---
name: Writing tests
category: Coding
description: Tests that fail for the reason they name, and cover the case that actually breaks.
when: writing tests, or asked whether existing tests are adequate.
default: false
---

A test must fail when the code is wrong. Before trusting a new test, break the
code deliberately and confirm it goes red. A test that passes either way is
worse than no test, because it advertises coverage that does not exist.

Test behaviour, not implementation. Assert on what the function returns or
changes, not on which private helper it called. A test coupled to internals
breaks on every refactor and blocks the cleanup it should be protecting.

One reason to fail per test. When a test asserts five unrelated things, its
name cannot describe the failure and a red result tells you little. Name the
test after the property: `test_missing_shard_reports_unavailable`, not
`test_config`.

Cover the boundaries, because that is where bugs live: empty input, a single
element, exactly the limit, one past the limit, `None`, a duplicate, a value
that resolves outside the allowed range. The happy path rarely breaks.

Prefer real objects to mocks. Mock what is slow, external, or nondeterministic
— the network, the clock, a GPU. Mocking your own code mostly tests that your
mock matches your assumption, which is the thing most likely to be wrong.

Use `tmp_path` for anything touching the filesystem, and make each test
independent — order dependence produces failures that vanish when you
investigate them.

For a bug fix, write the test first, watch it fail, then fix. That proves the
test detects the bug, which is the only evidence that it will catch a
regression later.

When testing a security boundary — path confinement, input validation — test
the attacks, not just the happy path: `../../etc/passwd`, an absolute path, a
symlink pointing outward, a URL that resolves to a private address.
