# Changelog

## 0.2.0 - No AMS MVP

- Created public fork development direction as ColorSplit3MF-Next.
- Added `noams_splitter.py` CLI for direct 3MF parsing and STL export.
- Added `noams_gui.py` Tkinter GUI for selecting 3MF files, inspecting colors, exporting STL files, and creating ZIP packages.
- Added support for `--info`, `--out`, `--format`, `--zip`, `--verbose`, and `--debug`.
- Added JSON export reports, text color summaries, and diagnostic reports.
- Added deterministic STL filenames based on source model and color/material key.
- Added automated pytest coverage for valid, invalid, single-color, multi-color, export, ZIP, and coordinate preservation cases.
- Added bilingual README, contributor guide, technical notes, roadmap, examples, and tests directory.

## 0.1.0 - Upstream Experimental Version

- Original experimental color splitting scripts from `mocsy/ColorSplit3mf`.
- Existing license and attribution preserved.
