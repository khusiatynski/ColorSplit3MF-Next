from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from noams_splitter import NoAmsSplitter, SplitterError, export_result, main
from noams_painter import recolor_3mf
from noams_gui import point_in_screen_triangle, remap_split_result_colors


def write_3mf(path: Path, object_model: str, main_model: str | None = None, metadata: dict[str, str] | None = None) -> None:
    if main_model is None:
        main_model = object_model
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("3D/3dmodel.model", main_model)
        if main_model != object_model:
            archive.writestr("3D/Objects/object_1.model", object_model)
        if metadata:
            for name, content in metadata.items():
                archive.writestr(name, content)


def simple_object_model(triangles: str, resources: str = "", object_id: str = "1") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    {resources}
    <object id="{object_id}" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/>
          <vertex x="1" y="0" z="0"/>
          <vertex x="0" y="1" z="0"/>
          <vertex x="0" y="0" z="1"/>
        </vertices>
        <triangles>{triangles}</triangles>
      </mesh>
    </object>
  </resources>
  <build><item objectid="{object_id}"/></build>
</model>
"""


def bambu_metadata() -> dict[str, str]:
    return {
        "Metadata/project_settings.config": json.dumps({"filament_colour": ["#FF0000", "#000000"]}),
        "Metadata/model_settings.config": """<?xml version="1.0" encoding="UTF-8"?>
<config><object id="2"><metadata key="extruder" value="1"/></object></config>""",
    }


def test_valid_3mf_single_color(tmp_path: Path) -> None:
    model = simple_object_model('<triangle v1="0" v2="1" v3="2"/>')
    input_file = tmp_path / "single.3mf"
    write_3mf(input_file, model)

    result = NoAmsSplitter(input_file).split()

    assert result.total_triangles == 1
    assert list(result.groups)[0] == "default"


def test_bambu_model_with_multiple_colors(tmp_path: Path) -> None:
    object_model = simple_object_model(
        '<triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="1" v3="3" paint_color="8"/>',
        object_id="1",
    )
    main_model = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter"
  xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
  xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
  <resources>
    <object id="2" type="model">
      <components>
        <component objectid="1" p:path="/3D/Objects/object_1.model"/>
      </components>
    </object>
  </resources>
  <build><item objectid="2"/></build>
</model>
"""
    input_file = tmp_path / "multi.3mf"
    write_3mf(input_file, object_model, main_model, bambu_metadata())

    result = NoAmsSplitter(input_file).split()

    assert set(result.groups) == {"#FF0000", "#000000"}
    assert result.groups["#FF0000"].triangle_count == 1
    assert result.groups["#000000"].triangle_count == 1


def test_standard_material_color_group(tmp_path: Path) -> None:
    resources = '<basematerials id="5"><base name="red" displaycolor="#FF0000FF"/></basematerials>'
    model = simple_object_model('<triangle v1="0" v2="1" v3="2" pid="5" p1="0"/>', resources)
    input_file = tmp_path / "material.3mf"
    write_3mf(input_file, model)

    result = NoAmsSplitter(input_file).split()

    assert set(result.groups) == {"#FF0000"}


def test_empty_file(tmp_path: Path) -> None:
    input_file = tmp_path / "empty.3mf"
    input_file.write_bytes(b"")

    with pytest.raises(SplitterError, match="empty"):
        NoAmsSplitter(input_file).split()


def test_corrupted_file(tmp_path: Path) -> None:
    input_file = tmp_path / "bad.3mf"
    input_file.write_bytes(b"not a zip")

    with pytest.raises(SplitterError, match="valid 3MF"):
        NoAmsSplitter(input_file).split()


def test_unsupported_format(tmp_path: Path) -> None:
    input_file = tmp_path / "model.stl"
    input_file.write_text("solid x\nendsolid x\n", encoding="utf-8")

    with pytest.raises(SplitterError, match="Unsupported"):
        NoAmsSplitter(input_file).split()


def test_stl_export(tmp_path: Path) -> None:
    model = simple_object_model('<triangle v1="0" v2="1" v3="2"/>')
    input_file = tmp_path / "model.3mf"
    write_3mf(input_file, model)
    result = NoAmsSplitter(input_file).split()

    exported = export_result(result, tmp_path / "out", "stl")

    assert len(exported) == 1
    assert exported[0].name == "model_default.stl"
    assert "facet normal" in exported[0].read_text(encoding="utf-8")


def test_coordinate_preservation_with_build_transform(tmp_path: Path) -> None:
    model = simple_object_model('<triangle v1="0" v2="1" v3="2"/>')
    model = model.replace('<item objectid="1"/>', '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 20 30"/>')
    input_file = tmp_path / "shifted.3mf"
    write_3mf(input_file, model)

    result = NoAmsSplitter(input_file).split()
    triangle = next(iter(result.groups.values())).triangles[0]

    assert triangle[0] == (10.0, 20.0, 30.0)
    assert triangle[1] == (11.0, 20.0, 30.0)
    assert triangle[2] == (10.0, 21.0, 30.0)


def test_cli_zip_and_debug(tmp_path: Path) -> None:
    model = simple_object_model('<triangle v1="0" v2="1" v3="2"/>')
    input_file = tmp_path / "model.3mf"
    out_dir = tmp_path / "export"
    write_3mf(input_file, model)

    status = main([str(input_file), "--out", str(out_dir), "--zip", "--debug"])

    assert status == 0
    assert (out_dir / "export_report.json").exists()
    assert (out_dir / "color_summary.txt").exists()
    assert (out_dir / "diagnostic_report.json").exists()
    assert out_dir.with_suffix(".zip").exists()


def test_recolor_bambu_project_palette(tmp_path: Path) -> None:
    object_model = simple_object_model(
        '<triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="1" v3="3" paint_color="8"/>',
        object_id="1",
    )
    main_model = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter"
  xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
  xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
  <resources>
    <object id="2" type="model">
      <components>
        <component objectid="1" p:path="/3D/Objects/object_1.model"/>
      </components>
    </object>
  </resources>
  <build><item objectid="2"/></build>
</model>
"""
    input_file = tmp_path / "bambu.3mf"
    output_file = tmp_path / "bambu_painted.3mf"
    write_3mf(input_file, object_model, main_model, bambu_metadata())

    changed = recolor_3mf(input_file, output_file, {"#FF0000": "#00FF00"})
    result = NoAmsSplitter(output_file).split()

    assert changed == 1
    assert set(result.groups) == {"#00FF00", "#000000"}


def test_recolor_standard_3mf_material(tmp_path: Path) -> None:
    resources = '<basematerials id="5"><base name="red" displaycolor="#FF0000FF"/></basematerials>'
    model = simple_object_model('<triangle v1="0" v2="1" v3="2" pid="5" p1="0"/>', resources)
    input_file = tmp_path / "material.3mf"
    output_file = tmp_path / "material_painted.3mf"
    write_3mf(input_file, model)

    changed = recolor_3mf(input_file, output_file, {"#FF0000": "#0000FF"})
    result = NoAmsSplitter(output_file).split()

    assert changed == 1
    assert set(result.groups) == {"#0000FF"}


def test_bambu_component_parts_use_part_extruders(tmp_path: Path) -> None:
    main_model = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/></vertices>
        <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
      </mesh>
    </object>
    <object id="2" type="model">
      <mesh>
        <vertices><vertex x="0" y="0" z="1"/><vertex x="1" y="0" z="1"/><vertex x="0" y="1" z="1"/></vertices>
        <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
      </mesh>
    </object>
    <object id="9" type="model">
      <components>
        <component objectid="1"/>
        <component objectid="2"/>
      </components>
    </object>
  </resources>
  <build><item objectid="9"/></build>
</model>
"""
    metadata = {
        "Metadata/project_settings.config": json.dumps({"filament_colour": ["#009300", "#966141"]}),
        "Metadata/model_settings.config": """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="9">
    <metadata key="extruder" value="1"/>
    <part id="1"><metadata key="source_volume_id" value="0"/><metadata key="extruder" value="2"/></part>
    <part id="2"><metadata key="source_volume_id" value="1"/></part>
  </object>
</config>""",
    }
    input_file = tmp_path / "component_parts.3mf"
    write_3mf(input_file, main_model, main_model, metadata)

    result = NoAmsSplitter(input_file).split()

    assert set(result.groups) == {"#009300", "#966141"}
    assert result.groups["#966141"].triangle_count == 1
    assert result.groups["#009300"].triangle_count == 1


def test_preview_export_color_remap_merges_groups(tmp_path: Path) -> None:
    object_model = simple_object_model(
        '<triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="1" v3="3" paint_color="8"/>',
        object_id="1",
    )
    main_model = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter"
  xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
  xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
  <resources>
    <object id="2" type="model">
      <components>
        <component objectid="1" p:path="/3D/Objects/object_1.model"/>
      </components>
    </object>
  </resources>
  <build><item objectid="2"/></build>
</model>
"""
    input_file = tmp_path / "multi.3mf"
    write_3mf(input_file, object_model, main_model, bambu_metadata())
    result = NoAmsSplitter(input_file).split()

    remapped = remap_split_result_colors(result, {"#FF0000": "#FFFFFF", "#000000": "#FFFFFF"})

    assert set(remapped.groups) == {"#FFFFFF"}
    assert remapped.groups["#FFFFFF"].triangle_count == 2


def test_point_in_screen_triangle() -> None:
    triangle = ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0))

    assert point_in_screen_triangle(2.0, 2.0, triangle)
    assert point_in_screen_triangle(0.0, 0.0, triangle)
    assert not point_in_screen_triangle(8.0, 8.0, triangle)
