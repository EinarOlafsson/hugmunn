---
name: Reproducibility & environments
category: Science
description: Pinning versions, recording provenance, and making an analysis rerunnable next year.
when: setting up an environment, installing a package, or producing a result someone will need to reproduce.
default: false
---

An analysis that only runs on one machine is a result nobody can check,
including you in six months.

Pin what matters. Record the versions of the packages that affect numbers —
numpy, scipy, scikit-image, cellpose, torch — not just the top-level tool.
`pip freeze` or `conda env export` captures the whole environment; a
`requirements.txt` with unpinned names captures almost nothing.

Never install into the environment a running analysis depends on. Package
managers resolve dependencies globally, so `pip install` of an unrelated tool
can silently downgrade numpy underneath a half-finished job. Make a new
environment instead.

Check what is actually imported, not what you think is. `python -c "import X;
print(X.__version__, X.__file__)"` catches the case where a conda package and a
pip package of the same name are both present and the wrong one wins.

Record the inputs, not only the code. A script that reads `data/latest.csv` is
not reproducible — `latest` changes. Reference a specific file, and record its
checksum or modification date alongside the result.

Set and record random seeds for anything stochastic — model initialisation,
train/test splits, augmentation, bootstrap resampling. An unseeded result
cannot be distinguished from a lucky one.

Write down the command that produced the output. The exact invocation with its
arguments, in the output directory or a log. "I ran the segmentation script"
is not recoverable six months later.

GPU results are not bitwise reproducible across driver or hardware changes even
with a fixed seed. Expect small numeric differences; if a conclusion depends on
them, the conclusion is too fragile.

State the environment when reporting a result that depends on it: package
versions, GPU, and whether it was CPU or GPU.
