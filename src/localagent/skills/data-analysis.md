---
name: Data analysis
category: Science
description: Statistical hygiene — what test, what n, and what the result does not show.
default: false
---

Establish what n is before anything else, and state it. n is the number of
independent biological units, not the number of measurements. Technical
replicates increase precision, not degrees of freedom.

Match the test to the design. Paired data needs a paired test. More than two
groups needs a single model with correction, not a series of pairwise tests.
Counts are not normal; proportions are not normal; log-normal data should be
analysed on the log scale or with a method that does not assume normality.

Plot the data, not just the summary. A bar of means hides bimodality, outliers,
and n. Show individual points when n is small enough to show them.

Report effect size with its confidence interval alongside any p-value. "p <
0.05" tells the reader a direction; the interval tells them whether the size
matters.

Distinguish planned comparisons from exploratory ones, and say which you are
doing. An exploratory finding is a hypothesis, not a result — it needs its own
experiment before it is a conclusion.

State what the analysis does not show. Correlation, a null result with low
power, and an effect measured in one cell line are all commonly over-read.
