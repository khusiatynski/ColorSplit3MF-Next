#!/usr/bin/env python3
"""Helpers for changing 3MF color definitions without changing geometry."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from noams_splitter import SplitterError, normalize_hex


def normalize_color_map(color_map: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for old_color, new_color in color_map.items():
        old_hex = normalize_hex(old_color)
        new_hex = normalize_hex(new_color)
        if not old_hex or not new_hex:
            raise SplitterError(f"Invalid color mapping: {old_color} -> {new_color}")
        normalized[old_hex] = new_hex
    return normalized


def recolor_3mf(input_file: str | Path, output_file: str | Path, color_map: dict[str, str]) -> int:
    """Copy a 3MF archive while replacing known color definitions.

    The function intentionally leaves triangle indices, geometry, transforms, and
    package structure untouched. It updates Bambu Studio filament palette JSON and
    standard 3MF material/color XML definitions when they match the provided HEX
    colors.
    """

    source = Path(input_file)
    target = Path(output_file)
    replacements = normalize_color_map(color_map)
    if not replacements:
        raise SplitterError("No color changes were provided.")
    if source.suffix.lower() != ".3mf":
        raise SplitterError("Input file must be a .3mf archive.")
    if target.suffix.lower() != ".3mf":
        raise SplitterError("Output file must use the .3mf extension.")
    if source.resolve() == target.resolve():
        raise SplitterError("Output file must be different from the input file.")

    changed = 0
    try:
        with zipfile.ZipFile(source, "r") as input_zip:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output_zip:
                for item in input_zip.infolist():
                    data = input_zip.read(item.filename)
                    if item.filename == "Metadata/project_settings.config":
                        data, count = _recolor_bambu_project_settings(data, replacements)
                        changed += count
                    elif item.filename.startswith("3D/") and item.filename.endswith(".model"):
                        data, count = _recolor_model_xml(data, replacements)
                        changed += count
                    output_zip.writestr(item, data)
    except zipfile.BadZipFile as exc:
        raise SplitterError("Input file is not a valid 3MF ZIP archive.") from exc
    except ET.ParseError as exc:
        raise SplitterError(f"3MF XML is corrupted: {exc}") from exc

    if changed == 0:
        raise SplitterError("No matching color definitions were found to change.")
    return changed


def _recolor_bambu_project_settings(data: bytes, replacements: dict[str, str]) -> tuple[bytes, int]:
    try:
        settings = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return data, 0

    changed = 0
    for key in ("filament_colour", "default_filament_colour", "extruder_colour"):
        values = settings.get(key)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            old_hex = normalize_hex(str(value))
            if old_hex and old_hex in replacements:
                values[index] = _replace_hex_preserving_alpha(str(value), replacements[old_hex])
                changed += 1

    if changed == 0:
        return data, 0
    return json.dumps(settings, indent=4).encode("utf-8"), changed


def _recolor_model_xml(data: bytes, replacements: dict[str, str]) -> tuple[bytes, int]:
    root = ET.fromstring(data)
    changed = 0
    for node in root.iter():
        for attribute in ("displaycolor", "color"):
            value = node.attrib.get(attribute)
            old_hex = normalize_hex(value)
            if old_hex and old_hex in replacements:
                node.attrib[attribute] = _replace_hex_preserving_alpha(value or "", replacements[old_hex])
                changed += 1

    if changed == 0:
        return data, 0
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), changed


def _replace_hex_preserving_alpha(original: str, new_hex: str) -> str:
    normalized = new_hex.upper()
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    old = original.strip()
    old_hex = normalize_hex(old)
    if old_hex and len(old.lstrip("#")) == 8:
        return f"{normalized}{old.lstrip('#')[6:8].upper()}"
    return normalized
