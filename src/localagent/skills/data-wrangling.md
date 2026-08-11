---
name: Data wrangling (pandas / SQL)
category: Science
description: Loading, joining, and aggregating tabular data without silently losing rows.
default: false
---

Look before you compute. Shape, dtypes, null counts, and the first few rows —
`python_exec` makes this cheap, and it catches the wrong file, the wrong
delimiter, and the header-read-as-data almost every time.

Check row counts across every join. An inner join that silently drops half the
data is the single most common way an analysis goes wrong, and the result still
looks plausible. Assert the count you expect:

```python
before = len(df)
merged = df.merge(other, on="well", how="left")
assert len(merged) == before, f"{before} -> {len(merged)}"
```

Know which join you want. `left` keeps everything on the left and fills gaps
with NaN; `inner` silently discards non-matches. Defaulting to `inner` because
it "looks cleaner" is discarding data.

Check the key before joining on it. Whitespace, case, and `A1` versus `A01` are
why a join returns nothing. Normalise explicitly rather than hoping.

Aggregate at the right level. Group to the biological replicate before testing,
not to the object. `groupby(['plate','well']).mean()` then compare wells — not
compare 40,000 individual objects as if independent.

NaN is not zero. `mean()` skips them, `sum()` treats them as zero, and a
comparison against NaN is always False. Decide what missing means and handle it
explicitly rather than letting each function decide differently.

Never chain assignment through a slice. `df[df.a > 1]['b'] = 0` writes to a
copy and silently does nothing. Use `.loc[mask, 'b'] = 0`.

Prefer SQL for filtering and aggregating a large database, pandas for reshaping
what you pulled back. Pulling a million rows into memory to compute a group
mean the database could compute is slow and unnecessary — `sql_schema` first,
then a query that does the work.

Report what you dropped. "Excluded 312 objects with area below threshold, 12
wells with fewer than 50 objects" is part of the result, not housekeeping.
