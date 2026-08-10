---
name: Microscopy & image analysis
category: Science
description: Conventions for quantitative fluorescence microscopy and segmentation work.
default: false
---

Segmentation is a means, not a result. The question is always what the objects
measure — count, intensity, area, colocalisation, spatial relationship. Ask what
is being quantified before proposing a pipeline.

Intensity comparisons are only valid across images acquired with identical
settings: exposure, gain, laser power, objective, binning. If any of those
differ between conditions, the comparison is invalid regardless of the
statistics applied to it.

Correct for background before quantifying, and say which method — rolling ball,
mode subtraction, or a paired unstained control. Report it; the choice changes
the numbers.

Segment on a channel that is not the channel you are measuring, wherever
possible. Segmenting and quantifying on the same signal builds the effect you
are testing for into the mask.

Per-cell measurements are not independent when cells share a field, a well, or a
coverslip. Aggregate to the biological replicate before testing, or use a model
that accounts for the nesting. A per-cell n of 4,000 from three wells is an n
of 3.

Report the object count that survived filtering, and what the filters were.
"Cells with area below X or intensity below Y were excluded" is part of the
result.

Plate layouts follow well-row-column conventions; keep a channel-to-marker
mapping in the metadata rather than in filenames alone, and never infer
biological identity from channel order.
