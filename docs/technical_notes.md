# Technical Notes

## Current Architecture

The stable MVP entry point is `noams_splitter.py`. The original experimental scripts remain in the repository for compatibility and reference.

## How 3MF Files Are Loaded

3MF files are ZIP archives. The MVP opens the archive with Python `zipfile`, reads every `3D/**/*.model` XML file, and parses those files with `xml.etree.ElementTree`.

The parser indexes:

- root model file, usually `3D/3dmodel.model`
- object model files, for example `3D/Objects/object_1.model`
- object resources by object ID
- component references using production extension `p:path`
- build items and component transforms

Build transforms are applied to vertices before export so generated STL files preserve world coordinates, scale, and orientation from the 3MF build plate placement.

## How Colors And Materials Are Extracted

The MVP supports two color sources:

- Bambu Studio / MakerWorld triangle attribute `paint_color`
- standard 3MF material references using triangle `pid` and `p1`

Bambu Studio stores filament colors in `Metadata/project_settings.config`. The observed convention in the sample model maps `paint_color` values to filament slots as `paint_color = filament_index * 4`. The parser uses this mapping when a filament palette is available and falls back to `paint_color_<id>` otherwise.

For standard 3MF resources, the parser indexes:

- `basematerials` / `base displaycolor`
- `colorgroup` / `color color`

Unpainted Bambu triangles use the object default extruder from `Metadata/model_settings.config` when available.

## How Meshes Are Grouped

Each triangle is transformed into world coordinates and assigned a color/material key. Triangles with the same key are grouped into one `ColorGroup`.

Groups are sorted by key before reporting and export so output is deterministic across runs.

## How Exports Are Generated

The MVP writes ASCII STL directly. Each detected color/material group becomes one STL file named from:

- source model stem
- normalized color/material key
- output format extension

Example:

```text
model_ff0000.stl
model_000000.stl
```

When `--zip` is used, the tool creates `output.zip` beside the selected output directory. The archive contains all generated STL files, `export_report.json`, and `color_summary.txt`.

## Supported File Formats

Input:

- `.3mf`

Output:

- `.stl`
- `.zip` package when requested

Legacy scripts may still support OBJ/PLY through third-party geometry libraries, but the stable MVP CLI currently exports STL only.

## Current Limitations

- Surface-color separation does not automatically create printable closed solids.
- Bambu `paint_color` mapping is based on current sample evidence and needs validation across more MakerWorld models.
- Texture-based colors are not extracted.
- Complex slicer modifiers and non-model metadata are reported only indirectly.
- Multiple build items are supported, but more real-world testing is needed.
- XML parsing is intentionally conservative and may skip malformed triangles with warnings.
