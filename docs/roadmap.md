# Roadmap

## MVP Stabilization

- Validate Bambu `paint_color` mapping against more MakerWorld profiles.
- Improve the Tkinter GUI with progress reporting and richer diagnostics.
- Replace sampled 2D orthographic preview with an interactive 3D renderer for per-triangle painting.
- Improve reports for object IDs, build item IDs, and source model paths.
- Add more sample 3MF fixtures with known expected output.
- Document workflows for printing separated parts without AMS.

## Planned Future Features

- drag & drop
- per-triangle 3D painting
- mesh repair
- automatic solid generation
- pin connectors
- magnet pockets
- assembly preview generation
- Bambu Studio integration
- PrusaSlicer integration
- batch processing
- Linux builds
- Windows installer
- macOS support

## Research Questions

- How often does painted surface data create non-watertight separated parts?
- Which slicer metadata should be preserved in reports for repeatable manual workflows?
- Can connector generation be automated without changing model dimensions unexpectedly?
