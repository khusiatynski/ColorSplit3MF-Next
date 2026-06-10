# Changelog

## 0.2.0 - No AMS MVP

- Created public fork development direction as ColorSplit3MF-Next.
- Added `noams_splitter.py` CLI for direct 3MF parsing and STL export.
- Added `noams_gui.py` Tkinter GUI for selecting 3MF files, inspecting colors, exporting STL files, and creating ZIP packages.
- Added sampled orthographic model preview with click-to-select color groups in the Tkinter GUI.
- Added lightweight interactive 3D preview controls for rotate, zoom, and pan.
- Added screen-space triangle picking against the full parsed mesh in the GUI preview.
- Added Pick/Mesh/Triangle GUI paint modes with per-triangle preview/STL color overrides.
- Added optional pywebview/WebGL 3D engine window that renders full parsed geometry with Three.js.
- Added `noams_painter.py` support for recoloring BambuLab-style 3MF filament palettes and standard 3MF material colors.
- Added Bambu Studio part/extruder color detection for 3MF files without per-triangle `paint_color`.
- Added support for `--info`, `--out`, `--format`, `--zip`, `--verbose`, and `--debug`.
- Added JSON export reports, text color summaries, and diagnostic reports.
- Added deterministic STL filenames based on source model and color/material key.
- Added automated pytest coverage for valid, invalid, single-color, multi-color, export, ZIP, and coordinate preservation cases.
- Added bilingual README, contributor guide, technical notes, roadmap, examples, and tests directory.

## 0.1.0 - Upstream Experimental Version

- Original experimental color splitting scripts from `mocsy/ColorSplit3mf`.
- Existing license and attribution preserved.
