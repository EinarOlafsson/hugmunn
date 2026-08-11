---
name: Experimental design
category: Science
description: Controls, replication, and blinding — what makes a result interpretable.
when: planning an experiment, or judging whether an existing result is interpretable.
default: false
---

Ask what result would falsify the hypothesis before designing anything. A
design that cannot produce a negative answer is not an experiment.

Name the controls explicitly, and check they control for the right thing:

- **Negative** — everything except the variable. Vehicle, untreated, scrambled
  guide, uninfected.
- **Positive** — a manipulation known to produce the effect. Without it, a null
  result is uninterpretable: you cannot tell "no effect" from "assay didn't work".
- **Matched** — controls that share the confound. If comparing secreted
  proteins to cytosolic ones, the comparison is partly about the secretory
  route; match on that or the route is what you measure.

Distinguish biological from technical replicates. Independent biological units
give you n. Measuring the same well three times improves precision and adds
nothing to your degrees of freedom. State both.

Randomise position. Plate edge effects, evaporation, and illumination gradients
are real, and a design where treatment occupies the left half of every plate
confounds treatment with position permanently.

Blind the scoring wherever a human judges the outcome. If blinding is
impractical, automate the measurement instead — an unblinded manual count is
the weakest evidence in most papers.

Decide the analysis before collecting data. Which comparison, which test, what
counts as the effect. Choosing after seeing the numbers is how a difference
that is not there becomes significant.

Power matters more than significance. An underpowered null result says nothing;
report the effect size you could have detected rather than implying absence of
evidence is evidence of absence.

For imaging specifically: acquire every condition with identical settings,
image the same number of fields per well, and check that the segmentation
performs equally on both conditions — a mask that works better on treated cells
manufactures a difference before any statistics run.
