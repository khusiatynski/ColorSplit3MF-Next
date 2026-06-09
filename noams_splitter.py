#!/usr/bin/env python3
"""Split colored 3MF models into separate printable STL parts."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS = {"m": CORE_NS, "p": PROD_NS}
LOGGER = logging.getLogger("noams_splitter")


Point = tuple[float, float, float]
Face = tuple[int, int, int]
Transform = tuple[float, float, float, float, float, float, float, float, float, float, float, float]


@dataclass(frozen=True)
class Triangle:
    """A triangle plus its source color/material key."""

    vertices: tuple[Point, Point, Point]
    color_key: str
    material_id: str


@dataclass
class ColorGroup:
    """All triangles assigned to one detected color or material."""

    key: str
    label: str
    material_id: str
    triangles: list[tuple[Point, Point, Point]] = field(default_factory=list)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)


@dataclass
class SplitResult:
    """Parsed 3MF output grouped by color/material."""

    input_file: Path
    unit: str
    groups: dict[str, ColorGroup]
    warnings: list[str]
    mesh_files: list[str]
    total_triangles: int
    total_vertices: int


class SplitterError(RuntimeError):
    """Raised when a model cannot be split."""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_transform(value: str | None) -> Transform:
    if not value:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    parts = [float(part) for part in value.split()]
    if len(parts) != 12:
        raise SplitterError(f"Invalid 3MF transform with {len(parts)} values")
    return tuple(parts)  # type: ignore[return-value]


def compose_transform(first: Transform, second: Transform) -> Transform:
    """Return a transform that applies first, then second."""

    def apply(matrix: Transform, point: Point) -> Point:
        return apply_transform(point, matrix)

    origin = apply(second, apply(first, (0.0, 0.0, 0.0)))
    x_axis = apply(second, apply(first, (1.0, 0.0, 0.0)))
    y_axis = apply(second, apply(first, (0.0, 1.0, 0.0)))
    z_axis = apply(second, apply(first, (0.0, 0.0, 1.0)))
    return (
        x_axis[0] - origin[0],
        x_axis[1] - origin[1],
        x_axis[2] - origin[2],
        y_axis[0] - origin[0],
        y_axis[1] - origin[1],
        y_axis[2] - origin[2],
        z_axis[0] - origin[0],
        z_axis[1] - origin[1],
        z_axis[2] - origin[2],
        origin[0],
        origin[1],
        origin[2],
    )


def apply_transform(point: Point, transform: Transform) -> Point:
    x, y, z = point
    return (
        transform[0] * x + transform[3] * y + transform[6] * z + transform[9],
        transform[1] * x + transform[4] * y + transform[7] * z + transform[10],
        transform[2] * x + transform[5] * y + transform[8] * z + transform[11],
    )


def normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", value)
    if not match:
        return None
    return f"#{match.group(1).upper()}"


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip("#").lower()).strip("_")
    return cleaned or "default"


def color_label(key: str) -> str:
    if key.startswith("#"):
        return key.upper()
    return key


class NoAmsSplitter:
    """Direct 3MF parser for color/material based mesh splitting."""

    def __init__(self, input_file: str | Path) -> None:
        self.input_file = Path(input_file)
        self.warnings: list[str] = []
        self.palette: list[str] = []
        self.default_extruders: dict[str, str] = {}
        self.part_extruders: dict[str, dict[int, str]] = {}
        self.resources: dict[tuple[str, str], ET.Element] = {}
        self.resource_paths: dict[str, str] = {}
        self.material_colors: dict[tuple[str, str], str] = {}
        self.mesh_files: list[str] = []
        self.unit = "millimeter"
        self.total_vertices = 0
        self.total_triangles = 0

    def split(self) -> SplitResult:
        if self.input_file.suffix.lower() != ".3mf":
            raise SplitterError("Unsupported input format. Expected a .3mf file.")
        if not self.input_file.exists():
            raise SplitterError(f"Input file not found: {self.input_file}")
        if self.input_file.stat().st_size == 0:
            raise SplitterError("Input file is empty.")

        try:
            with zipfile.ZipFile(self.input_file) as package:
                self.palette = self._read_bambu_palette(package)
                self.default_extruders = self._read_default_extruders(package)
                roots = self._read_model_roots(package)
                main_root = roots.get("3D/3dmodel.model") or next(iter(roots.values()))
                self.unit = main_root.attrib.get("unit", self.unit)
                self._index_resources(roots)
                triangles = self._collect_build_triangles(main_root)
        except zipfile.BadZipFile as exc:
            raise SplitterError("Input file is not a valid 3MF ZIP archive.") from exc
        except ET.ParseError as exc:
            raise SplitterError(f"3MF XML is corrupted: {exc}") from exc

        groups = self._group_triangles(triangles)
        if not groups:
            raise SplitterError("No triangles were found in the 3MF model.")

        return SplitResult(
            input_file=self.input_file,
            unit=self.unit,
            groups=dict(sorted(groups.items())),
            warnings=self.warnings,
            mesh_files=sorted(self.mesh_files),
            total_triangles=self.total_triangles,
            total_vertices=self.total_vertices,
        )

    def _read_model_roots(self, package: zipfile.ZipFile) -> dict[str, ET.Element]:
        roots: dict[str, ET.Element] = {}
        for name in package.namelist():
            if name.startswith("3D/") and name.endswith(".model"):
                roots[name] = ET.fromstring(package.read(name))
        if not roots:
            raise SplitterError("No 3MF .model files found.")
        self.mesh_files = sorted(roots)
        return roots

    def _read_bambu_palette(self, package: zipfile.ZipFile) -> list[str]:
        try:
            raw = package.read("Metadata/project_settings.config").decode("utf-8")
            settings = json.loads(raw)
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return []

        colors: list[str] = []
        for key in ("filament_colour", "default_filament_colour", "extruder_colour"):
            values = settings.get(key, [])
            if isinstance(values, list):
                for value in values:
                    color = normalize_hex(str(value))
                    if color and color not in colors:
                        colors.append(color)
        return colors

    def _read_default_extruders(self, package: zipfile.ZipFile) -> dict[str, str]:
        try:
            root = ET.fromstring(package.read("Metadata/model_settings.config"))
        except (KeyError, ET.ParseError):
            return {}

        defaults: dict[str, str] = {}
        for object_node in root.findall("object"):
            object_id = object_node.attrib.get("id")
            if not object_id:
                continue
            for metadata in object_node.findall("metadata"):
                if metadata.attrib.get("key") == "extruder" and metadata.attrib.get("value"):
                    defaults[object_id] = metadata.attrib["value"]
            inherited = defaults.get(object_id)
            part_map: dict[int, str] = {}
            for part in object_node.findall("part"):
                volume_index = self._part_volume_index(part)
                if volume_index is None:
                    continue
                extruder = self._part_extruder(part) or inherited
                if extruder:
                    part_map[volume_index] = extruder
            if part_map:
                self.part_extruders[object_id] = part_map
        return defaults

    def _part_volume_index(self, part: ET.Element) -> int | None:
        for metadata in part.findall("metadata"):
            if metadata.attrib.get("key") == "source_volume_id" and metadata.attrib.get("value") is not None:
                try:
                    return int(metadata.attrib["value"])
                except ValueError:
                    return None
        part_id = part.attrib.get("id")
        if part_id and part_id.isdigit():
            return int(part_id) - 1
        return None

    def _part_extruder(self, part: ET.Element) -> str | None:
        for metadata in part.findall("metadata"):
            if metadata.attrib.get("key") == "extruder" and metadata.attrib.get("value"):
                return metadata.attrib["value"]
        return None

    def _index_resources(self, roots: dict[str, ET.Element]) -> None:
        for path, root in roots.items():
            self._index_materials(root)
            for object_node in root.findall(".//m:object", NS):
                object_id = object_node.attrib.get("id")
                if object_id:
                    self.resources[(path, object_id)] = object_node
                    self.resource_paths[object_id] = path

    def _index_materials(self, root: ET.Element) -> None:
        for resource in root.findall(".//m:resources/*", NS):
            resource_id = resource.attrib.get("id")
            if not resource_id:
                continue
            name = local_name(resource.tag)
            if name == "basematerials":
                for index, base in enumerate(resource.findall("m:base", NS)):
                    color = normalize_hex(base.attrib.get("displaycolor"))
                    if color:
                        self.material_colors[(resource_id, str(index))] = color
            elif name == "colorgroup":
                for index, color_node in enumerate(resource.findall("m:color", NS)):
                    color = normalize_hex(color_node.attrib.get("color"))
                    if color:
                        self.material_colors[(resource_id, str(index))] = color

    def _collect_build_triangles(self, main_root: ET.Element) -> list[Triangle]:
        triangles: list[Triangle] = []
        build_items = main_root.findall(".//m:build/m:item", NS)
        if not build_items:
            self.warnings.append("No build items found; parsing model resources directly.")
            for (path, object_id), resource in sorted(self.resources.items()):
                triangles.extend(self._collect_object_triangles(path, object_id, resource, parse_transform(None), object_id))
            return triangles

        for item in build_items:
            object_id = item.attrib.get("objectid")
            if not object_id:
                continue
            path = self.resource_paths.get(object_id, "3D/3dmodel.model")
            resource = self.resources.get((path, object_id))
            if resource is None:
                self.warnings.append(f"Build item references missing object {object_id}.")
                continue
            transform = parse_transform(item.attrib.get("transform"))
            triangles.extend(self._collect_object_triangles(path, object_id, resource, transform, object_id))
        return triangles

    def _collect_object_triangles(
        self,
        path: str,
        object_id: str,
        object_node: ET.Element,
        transform: Transform,
        default_object_id: str,
        extruder_override: str | None = None,
    ) -> list[Triangle]:
        mesh_node = object_node.find("m:mesh", NS)
        if mesh_node is not None:
            return self._collect_mesh_triangles(mesh_node, transform, default_object_id, extruder_override)

        triangles: list[Triangle] = []
        for component_index, component in enumerate(object_node.findall(".//m:component", NS)):
            component_id = component.attrib.get("objectid")
            if not component_id:
                continue
            component_path = component.attrib.get(f"{{{PROD_NS}}}path", path).lstrip("/")
            resource = self.resources.get((component_path, component_id))
            if resource is None:
                fallback_path = self.resource_paths.get(component_id)
                resource = self.resources.get((fallback_path or path, component_id))
                component_path = fallback_path or component_path
            if resource is None:
                self.warnings.append(f"Component references missing object {component_id}.")
                continue
            combined = compose_transform(parse_transform(component.attrib.get("transform")), transform)
            component_extruder = self._component_extruder(default_object_id, component_index, extruder_override)
            triangles.extend(
                self._collect_object_triangles(
                    component_path,
                    component_id,
                    resource,
                    combined,
                    default_object_id,
                    component_extruder,
                )
            )
        return triangles

    def _collect_mesh_triangles(
        self,
        mesh_node: ET.Element,
        transform: Transform,
        default_object_id: str,
        extruder_override: str | None = None,
    ) -> list[Triangle]:
        vertices: list[Point] = []
        for vertex in mesh_node.findall(".//m:vertices/m:vertex", NS):
            vertices.append(
                (
                    float(vertex.attrib["x"]),
                    float(vertex.attrib["y"]),
                    float(vertex.attrib["z"]),
                )
            )

        self.total_vertices += len(vertices)
        triangles: list[Triangle] = []
        default_key, default_material = self._default_color_for_object(default_object_id, extruder_override)

        for triangle in mesh_node.findall(".//m:triangles/m:triangle", NS):
            try:
                face = (int(triangle.attrib["v1"]), int(triangle.attrib["v2"]), int(triangle.attrib["v3"]))
                points = tuple(apply_transform(vertices[index], transform) for index in face)
            except (KeyError, IndexError, ValueError) as exc:
                self.warnings.append(f"Skipped malformed triangle: {exc}")
                continue

            key, material_id = self._triangle_color(triangle, default_key, default_material)
            triangles.append(Triangle(points, key, material_id))  # type: ignore[arg-type]
            self.total_triangles += 1
        return triangles

    def _component_extruder(
        self,
        object_id: str,
        component_index: int,
        inherited_extruder: str | None = None,
    ) -> str | None:
        part_extruder = self.part_extruders.get(object_id, {}).get(component_index)
        return part_extruder or inherited_extruder or self.default_extruders.get(object_id)

    def _default_color_for_object(self, object_id: str, extruder_override: str | None = None) -> tuple[str, str]:
        extruder = extruder_override or self.default_extruders.get(object_id)
        if extruder and extruder.isdigit():
            index = int(extruder) - 1
            if 0 <= index < len(self.palette):
                return self.palette[index], f"extruder_{extruder}"
            return f"material_{extruder}", f"extruder_{extruder}"
        if self.palette:
            return self.palette[0], "extruder_1"
        return "default", "default"

    def _triangle_color(self, triangle: ET.Element, default_key: str, default_material: str) -> tuple[str, str]:
        paint_color = triangle.attrib.get("paint_color")
        if paint_color is not None:
            color = self._color_from_bambu_paint_id(paint_color)
            return color, f"paint_color_{paint_color}"

        pid = triangle.attrib.get("pid")
        p1 = triangle.attrib.get("p1")
        if pid is not None:
            material = f"pid_{pid}"
            index = p1 or "0"
            color = self.material_colors.get((pid, index))
            return color or f"material_{pid}_{index}", material

        return default_key, default_material

    def _color_from_bambu_paint_id(self, paint_color: str) -> str:
        if paint_color.isdigit():
            value = int(paint_color)
            if value > 0 and value % 4 == 0:
                index = (value // 4) - 1
                if 0 <= index < len(self.palette):
                    return self.palette[index]
        return f"paint_color_{paint_color}"

    def _group_triangles(self, triangles: Iterable[Triangle]) -> dict[str, ColorGroup]:
        groups: dict[str, ColorGroup] = {}
        for triangle in triangles:
            group = groups.setdefault(
                triangle.color_key,
                ColorGroup(
                    key=triangle.color_key,
                    label=color_label(triangle.color_key),
                    material_id=triangle.material_id,
                ),
            )
            group.triangles.append(triangle.vertices)
        return groups


def triangle_normal(triangle: tuple[Point, Point, Point]) -> Point:
    a, b, c = triangle
    ux, uy, uz = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    vx, vy, vz = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def write_ascii_stl(path: Path, name: str, triangles: Iterable[tuple[Point, Point, Point]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"solid {safe_name(name)}\n")
        for triangle in triangles:
            normal = triangle_normal(triangle)
            handle.write(f"  facet normal {normal[0]:.9g} {normal[1]:.9g} {normal[2]:.9g}\n")
            handle.write("    outer loop\n")
            for vertex in triangle:
                handle.write(f"      vertex {vertex[0]:.9g} {vertex[1]:.9g} {vertex[2]:.9g}\n")
            handle.write("    endloop\n")
            handle.write("  endfacet\n")
        handle.write(f"endsolid {safe_name(name)}\n")


def result_summary(result: SplitResult) -> dict[str, Any]:
    return {
        "input": str(result.input_file),
        "unit": result.unit,
        "detected_colors": len(result.groups),
        "total_triangles": result.total_triangles,
        "total_vertices": result.total_vertices,
        "mesh_files": result.mesh_files,
        "colors": [
            {
                "key": group.key,
                "label": group.label,
                "material_id": group.material_id,
                "triangles": group.triangle_count,
            }
            for group in result.groups.values()
        ],
        "warnings": result.warnings,
    }


def write_report(path: Path, result: SplitResult, exported_files: list[Path]) -> None:
    report = result_summary(result)
    report["exported_files"] = [file.name for file in exported_files]
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def write_color_summary(path: Path, result: SplitResult) -> None:
    lines = [
        f"Input: {result.input_file}",
        f"Unit: {result.unit}",
        f"Detected colors: {len(result.groups)}",
        f"Total triangles: {result.total_triangles}",
        "",
    ]
    for group in result.groups.values():
        lines.append(f"{group.label}: {group.triangle_count} triangles ({group.material_id})")
    if result.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in result.warnings)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_result(result: SplitResult, output_dir: Path, output_format: str) -> list[Path]:
    if output_format != "stl":
        raise SplitterError("Only STL export is implemented in the MVP.")

    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = safe_name(result.input_file.stem)
    exported: list[Path] = []
    for group in result.groups.values():
        filename = f"{base_name}_{safe_name(group.label)}.{output_format}"
        path = output_dir / filename
        write_ascii_stl(path, f"{base_name}_{group.label}", group.triangles)
        exported.append(path)
    return exported


def create_zip_archive(output_dir: Path, exported_files: list[Path], report_file: Path, summary_file: Path) -> Path:
    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in exported_files:
            archive.write(file, file.name)
        archive.write(report_file, report_file.name)
        archive.write(summary_file, summary_file.name)
    return zip_path


def log_summary(result: SplitResult) -> None:
    LOGGER.info("Detected colors: %s", len(result.groups))
    for group in result.groups.values():
        LOGGER.info("%s: %s triangles", group.label, group.triangle_count)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Split multi-color 3MF models for No AMS FDM printing.")
    parser.add_argument("input_file", help="Input .3mf file")
    parser.add_argument("--info", action="store_true", help="Show detected colors/materials without exporting")
    parser.add_argument("--out", default="output", help="Output directory")
    parser.add_argument("--format", default="stl", choices=["stl"], help="Export format")
    parser.add_argument("--zip", action="store_true", help="Create output.zip with STL files and reports")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--debug", action="store_true", help="Write diagnostic_report.json")
    return parser


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        result = NoAmsSplitter(args.input_file).split()
        log_summary(result)

        if args.info:
            print(json.dumps(result_summary(result), indent=2))
            return 0

        output_dir = Path(args.out)
        exported_files = export_result(result, output_dir, args.format)
        report_file = output_dir / "export_report.json"
        summary_file = output_dir / "color_summary.txt"
        write_report(report_file, result, exported_files)
        write_color_summary(summary_file, result)

        if args.debug:
            debug_file = output_dir / "diagnostic_report.json"
            write_report(debug_file, result, exported_files)
            LOGGER.info("Diagnostic report: %s", debug_file)

        if args.zip:
            zip_path = create_zip_archive(output_dir, exported_files, report_file, summary_file)
            LOGGER.info("ZIP export: %s", zip_path)

        LOGGER.info("Exported %s STL file(s) to %s", len(exported_files), output_dir)
        return 0
    except SplitterError as exc:
        LOGGER.error("%s", exc)
        return 2
    except OSError as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
