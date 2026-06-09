# ColorSplit3MF-Next

Experimental open-source utility for splitting multi-color 3MF models into separate printable parts for FDM printing without AMS or other multi-material hardware.

This repository is a public fork of [mocsy/ColorSplit3mf](https://github.com/mocsy/ColorSplit3mf). Original author attribution, history, and license information are preserved. Current development focuses on research, testing, and practical "No AMS" workflows for Bambu Studio / MakerWorld style 3MF files.

## Screenshots

Screenshots and workflow captures will be added as the MVP stabilizes.

Existing upstream sample images are available in `content/`.

## Installation

```bash
git clone https://github.com/khusiatynski/ColorSplit3MF-Next.git
cd ColorSplit3MF-Next
python -m pip install -e ".[dev]"
```

The MVP splitter itself uses Python standard library modules for parsing and STL export. The legacy experimental scripts may still require their original optional geometry dependencies.

## Usage

Start the desktop GUI:

```bash
python noams_gui.py
```

Or, after installation:

```bash
noams-splitter-gui
```

The GUI includes file selection, color detection, a sampled orthographic model preview, color-group selection by clicking the preview, color remapping, painted 3MF export, STL splitting, and ZIP export.

Show detected colors/materials:

```bash
python noams_splitter.py model.3mf --info
```

Export separate STL files:

```bash
python noams_splitter.py model.3mf --out output --format stl
```

Export STL files plus reports in `output.zip`:

```bash
python noams_splitter.py model.3mf --out output --format stl --zip
```

Write diagnostic details:

```bash
python noams_splitter.py model.3mf --out output --debug --verbose
```

Typical output filenames are deterministic:

```text
model_ff0000.stl
model_000000.stl
model_ffffff.stl
```

## Supported Formats

Input:

- `.3mf`
- Bambu Studio / MakerWorld 3MF archives with triangle `paint_color` attributes
- BambuLab-style filament palettes stored in `Metadata/project_settings.config`
- Standard 3MF `basematerials` and `colorgroup` colors for triangle `pid` / `p1` references

Output:

- `.stl` ASCII STL per detected color/material
- Optional `.zip` containing STL files, `export_report.json`, and `color_summary.txt`
- Painted `.3mf` files from the GUI color editor

## Known Limitations

- This is an experimental MVP, not a slicer replacement.
- STL output contains surface triangles only; it does not automatically generate watertight solids from painted surface regions.
- Bambu `paint_color` to filament mapping uses the observed `paint_color = filament_index * 4` convention and may need more samples.
- Complex modifier meshes, slicer-only settings, texture colors, and non-triangle geometry are not fully supported.
- Only STL export is implemented in the stable MVP CLI.

## Roadmap

See [docs/roadmap.md](docs/roadmap.md).

## Development

```bash
python -m pytest
```

Primary development branch:

```text
dev/noams-mvp
```

## Polski

# ColorSplit3MF-Next

Eksperymentalne narzedzie open-source do dzielenia wielokolorowych modeli 3MF na osobne czesci STL, ktore mozna drukowac na drukarce FDM bez AMS lub innego systemu multi-material.

Repozytorium jest publicznym forkiem projektu [mocsy/ColorSplit3mf](https://github.com/mocsy/ColorSplit3mf). Zachowano autora oryginalnego projektu, historie repozytorium oraz informacje licencyjne.

## Instalacja

```bash
git clone https://github.com/khusiatynski/ColorSplit3MF-Next.git
cd ColorSplit3MF-Next
python -m pip install -e ".[dev]"
```

## Przyklady Uzycia

Uruchomienie prostego GUI:

```bash
python noams_gui.py
```

Albo po instalacji:

```bash
noams-splitter-gui
```

GUI zawiera wybor pliku, wykrywanie kolorow, probkowany podglad ortograficzny modelu, wybor grupy koloru kliknieciem w podglad, zmiane kolorow, zapis przemalowanego 3MF, dzielenie na STL i eksport ZIP.

Wyswietlenie informacji o kolorach:

```bash
python noams_splitter.py model.3mf --info
```

Eksport osobnych plikow STL:

```bash
python noams_splitter.py model.3mf --out output --format stl
```

Eksport ZIP z raportami:

```bash
python noams_splitter.py model.3mf --out output --format stl --zip
```

## Obslugiwane Formaty

Wejscie:

- `.3mf`
- pliki 3MF z Bambu Studio / MakerWorld z atrybutem `paint_color`
- palety filamentow BambuLab zapisane w `Metadata/project_settings.config`
- standardowe materialy 3MF `basematerials` i `colorgroup`

Wyjscie:

- osobne pliki `.stl`
- opcjonalny `.zip` z plikami STL i raportami
- przemalowane pliki `.3mf` z edytora GUI

## Znane Ograniczenia

- To MVP badawcze, nie zamiennik slicera.
- Eksport STL dzieli powierzchnie wedlug kolorow, ale nie tworzy automatycznie zamknietych bryl.
- Mapowanie `paint_color` z Bambu Studio wymaga dalszych testow na wiekszej liczbie modeli.
- Na razie stabilny CLI eksportuje tylko STL.

## Plan Rozwoju

Zobacz [docs/roadmap.md](docs/roadmap.md).
