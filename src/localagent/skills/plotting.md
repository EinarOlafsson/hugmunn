---
name: Figures & plots
category: Writing
description: Matplotlib figures that show the data honestly and survive a reviewer.
when: producing a chart, figure, or any visual summary of data.
default: false
---

Show the data, not only a summary of it. A bar of means hides sample size,
spread, bimodality, and outliers — every one of which changes the
interpretation. Overlay individual points whenever n is small enough to see
them, which for most experiments it is.

Pick the form from the question:

| Question | Form |
|---|---|
| How does a distribution look? | histogram, or strip/swarm over a box |
| Do two groups differ? | points + mean ± CI, not a bar |
| How does x relate to y? | scatter, with a fit only if a model is justified |
| How does something change over time? | line, one per replicate |
| How do many conditions compare? | small multiples on shared axes |

Never truncate a bar-chart y-axis at a non-zero value. Bar length encodes
magnitude, so a cropped axis exaggerates a difference. Truncating a *line* or
scatter axis is fine — position, not length, is the encoding.

Label axes with units. "Area" is not a label; "Cell area (µm²)" is. Say in the
legend what error bars are — SD, SEM, and 95% CI look identical and mean very
different things.

Use colour for a variable, not decoration, and keep the palette consistent
across panels of the same figure. Do not encode information in colour alone;
around 8% of men cannot distinguish red from green, and journals print in
greyscale. Use shape or position as a second channel.

Avoid rainbow and jet colormaps for continuous data — they create edges that
are not in the data. Use `viridis` or `magma`, which are perceptually uniform
and survive greyscale conversion.

Save vector (PDF/SVG) for anything with text or lines, and 300+ dpi PNG only
for images. A rasterised plot in a manuscript looks like a screenshot because
it is one.

Set the figure size to the final printed size and scale fonts to match, rather
than shrinking a large figure into a column and making the labels unreadable.
