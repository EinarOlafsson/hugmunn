---
name: File formats
category: Science
description: The quirks of CSV, TIFF, HDF5 and friends that silently corrupt data.
when: reading or writing a data file, or a file loads but the contents look wrong.
default: false
---

Most "the data is wrong" bugs are a format assumption, not a logic error.

**CSV.** No type information, so everything is guessed. A leading-zero well ID
`A01` may survive but a plate `007` becomes `7`; a gene named `MARCH1` becomes a
date in spreadsheet software; a large integer ID becomes a float and loses
precision. Read identifier columns as strings explicitly. Check the delimiter
and decimal separator — European locales use `;` and `,` and a comma-decimal
file read as comma-separated silently doubles the column count.

**TIFF.** The microscopy default and the most misread. A multi-page TIFF is a
z-stack, a time series, or channels, and nothing in the file says which — the
axis order is a convention of the acquisition software. Bit depth matters: 16-bit
data read as 8-bit clips every intensity above 255, which quietly destroys the
dynamic range you acquired. Check `image_info` before assuming.

**JSON.** No integer/float distinction on the way out in some writers, no
comments, no trailing commas, and NaN is not valid JSON — writers emit `NaN`
anyway and strict parsers then reject the file.

**HDF5 / NPZ.** Good for arrays, but an HDF5 file written while another process
holds it open can be silently truncated. Close handles; do not read a file that
is still being written.

**Excel.** Dates, autocorrect, and locale conversion happen on open *and* on
save, so a file that round-trips through Excel is not the file you wrote. Never
use it as an intermediate format in a pipeline.

**Paths and encoding.** Assume UTF-8, pass `encoding="utf-8"` explicitly, and
use `errors="replace"` when reading files you did not write. Windows-authored
text files carry `\r\n`; a trailing `\r` on a parsed field breaks equality
comparisons in a way that is invisible when printed.

Verify after loading, always: shape, dtypes, the first rows, and the range of
any numeric column. Thirty seconds there saves an entire analysis built on a
column read as text.
