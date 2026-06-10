#!/usr/bin/env python3
"""Generate a local WebGL/Three.js viewer for parsed 3MF models."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import sys
import webbrowser
from array import array
from pathlib import Path
from typing import Any

from noams_splitter import NoAmsSplitter, SplitResult, SplitterError


THREE_VERSION = "0.165.0"
VIEWER_CACHE_DIR = Path(__file__).with_name(".viewer_cache")
THREE_MODULE = Path(__file__).with_name("vendor") / "three" / "three.module.js"
ORBIT_CONTROLS = Path(__file__).with_name("vendor") / "three" / "OrbitControls.js"


def _float32_base64(values: list[float]) -> str:
    floats = array("f", values)
    if sys.byteorder != "little":
        floats.byteswap()
    return base64.b64encode(floats.tobytes()).decode("ascii")


def _viewer_payload(result: SplitResult) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for group in result.groups.values():
        values: list[float] = []
        for triangle in group.triangles:
            for vertex in triangle:
                values.extend(vertex)
        groups.append(
            {
                "label": group.label,
                "materialId": group.material_id,
                "color": group.label if group.label.startswith("#") and len(group.label) == 7 else "#9E9E9E",
                "triangles": group.triangle_count,
                "vertices": _float32_base64(values),
            }
        )
    return {
        "input": str(result.input_file),
        "unit": result.unit,
        "totalTriangles": result.total_triangles,
        "groups": groups,
    }


def _cached_viewer_path(input_file: str | Path) -> Path:
    source = Path(input_file)
    stat = source.stat()
    digest = hashlib.sha1(f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
    return VIEWER_CACHE_DIR / f"{source.stem}_{digest}.viewer.html"


def generate_webgl_viewer(input_file: str | Path, output_file: str | Path | None = None, use_cache: bool = True) -> Path:
    output_path = Path(output_file) if output_file else _cached_viewer_path(input_file)
    if use_cache and output_path.exists():
        return output_path

    result = NoAmsSplitter(input_file).split()
    payload = _viewer_payload(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_html_document(payload), encoding="utf-8")
    return output_path


def open_webgl_app_window(input_file: str | Path, output_file: str | Path | None = None) -> Path:
    viewer = generate_webgl_viewer(input_file, output_file)
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SplitterError("pywebview is not installed. Install with: python -m pip install -e .[viewer]") from exc

    webview.create_window(
        "ColorSplit3MF WebGL Viewer",
        viewer.resolve().as_uri(),
        width=1320,
        height=860,
        resizable=True,
    )
    webview.start()
    return viewer


def _html_document(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"))
    title = html.escape(Path(payload["input"]).name)
    three_module = THREE_MODULE.resolve().as_uri() if THREE_MODULE.exists() else f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js"
    orbit_controls = ORBIT_CONTROLS.resolve().as_uri() if ORBIT_CONTROLS.exists() else f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/controls/OrbitControls.js"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ColorSplit3MF WebGL Viewer - {title}</title>
  <style>
    html, body {{ margin: 0; height: 100%; overflow: hidden; background: #111418; color: #e8edf2; font-family: system-ui, Segoe UI, sans-serif; }}
    #viewport {{ position: fixed; inset: 0; }}
    #panel {{ position: fixed; left: 16px; top: 16px; width: 320px; max-height: calc(100vh - 32px); overflow: auto; background: rgba(20, 24, 30, .88); border: 1px solid rgba(255,255,255,.12); border-radius: 8px; box-shadow: 0 16px 48px rgba(0,0,0,.32); }}
    #panel header {{ padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,.10); }}
    #panel h1 {{ margin: 0 0 4px; font-size: 15px; font-weight: 650; }}
    #panel .meta {{ font-size: 12px; color: #aeb8c4; }}
    #tools {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,.10); }}
    button, input[type=color] {{ height: 30px; border-radius: 6px; border: 1px solid rgba(255,255,255,.16); background: #202833; color: #edf2f7; }}
    button:hover {{ background: #2a3544; }}
    #groups {{ padding: 8px; }}
    .group {{ display: grid; grid-template-columns: 22px 1fr auto; gap: 8px; align-items: center; padding: 7px 6px; border-radius: 6px; cursor: pointer; }}
    .group:hover, .group.active {{ background: rgba(255,255,255,.08); }}
    .swatch {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid rgba(255,255,255,.28); }}
    .label {{ font-size: 13px; }}
    .count {{ font-size: 12px; color: #aeb8c4; }}
    #status {{ position: fixed; left: 16px; bottom: 16px; right: 16px; min-height: 22px; padding: 8px 10px; border-radius: 6px; background: rgba(20,24,30,.82); color: #dce5ee; font-size: 13px; pointer-events: none; }}
    #help {{ grid-column: 1 / -1; color: #aeb8c4; font-size: 12px; line-height: 1.35; }}
  </style>
</head>
<body>
  <div id="viewport"></div>
  <aside id="panel">
    <header>
      <h1>{title}</h1>
      <div class="meta" id="summary"></div>
    </header>
    <section id="tools">
      <button id="fit">Fit</button>
      <button id="wire">Wireframe</button>
      <input type="color" id="paint" value="#ff0000" title="Paint color">
      <button id="paintFace">Paint picked face</button>
      <div id="help">Mouse: rotate / zoom / pan. Click a triangle for real raycast picking. This viewer renders full parsed geometry in WebGL.</div>
    </section>
    <section id="groups"></section>
  </aside>
  <div id="status">Loading geometry...</div>
  <script type="application/json" id="payload">{payload_json}</script>
  <script type="module">
    import * as THREE from '{three_module}';
    import {{ OrbitControls }} from '{orbit_controls}';

    const payload = JSON.parse(document.getElementById('payload').textContent);
    const viewport = document.getElementById('viewport');
    const status = document.getElementById('status');
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111418);

    const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 100000);
    const renderer = new THREE.WebGLRenderer({{ antialias: true, powerPreference: 'high-performance' }});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    viewport.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x44505f, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(1, -1.4, 2.2);
    scene.add(key);

    const root = new THREE.Group();
    const meshes = [];
    let picked = null;
    scene.add(root);

    function decodeFloat32(base64) {{
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new Float32Array(bytes.buffer);
    }}

    for (const group of payload.groups) {{
      const geometry = new THREE.BufferGeometry();
      const positions = decodeFloat32(group.vertices);
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      geometry.computeBoundingSphere();
      const material = new THREE.MeshStandardMaterial({{
        color: group.color,
        roughness: 0.72,
        metalness: 0.0,
        side: THREE.DoubleSide,
      }});
      const mesh = new THREE.Mesh(geometry, material);
      mesh.userData.group = group;
      root.add(mesh);
      meshes.push(mesh);
    }}

    const groupsEl = document.getElementById('groups');
    for (const mesh of meshes) {{
      const group = mesh.userData.group;
      const row = document.createElement('div');
      row.className = 'group';
      row.innerHTML = `<span class="swatch" style="background:${{group.color}}"></span><span class="label">${{group.label}}</span><span class="count">${{group.triangles.toLocaleString()}}</span>`;
      row.onclick = () => {{
        mesh.visible = !mesh.visible;
        row.classList.toggle('active', mesh.visible);
      }};
      row.classList.add('active');
      groupsEl.appendChild(row);
    }}

    document.getElementById('summary').textContent = `${{payload.totalTriangles.toLocaleString()}} triangles, ${{payload.groups.length}} color groups, unit: ${{payload.unit}}`;

    function fitCamera() {{
      const box = new THREE.Box3().setFromObject(root);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(size.x, size.y, size.z) || 1;
      controls.target.copy(center);
      camera.position.set(center.x + radius * 1.2, center.y - radius * 1.5, center.z + radius * 1.0);
      camera.near = Math.max(radius / 10000, 0.01);
      camera.far = radius * 100;
      camera.updateProjectionMatrix();
      controls.update();
    }}

    document.getElementById('fit').onclick = fitCamera;
    document.getElementById('wire').onclick = () => {{
      for (const mesh of meshes) mesh.material.wireframe = !mesh.material.wireframe;
    }};

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    renderer.domElement.addEventListener('click', (event) => {{
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(mouse, camera);
      const hits = raycaster.intersectObjects(meshes.filter(m => m.visible), false);
      if (!hits.length) return;
      picked = hits[0];
      const group = picked.object.userData.group;
      status.textContent = `Picked face ${{picked.faceIndex}} in ${{group.label}} (${{group.triangles.toLocaleString()}} triangles)`;
    }});

    document.getElementById('paintFace').onclick = () => {{
      if (!picked) return;
      picked.object.material.color.set(document.getElementById('paint').value);
      status.textContent = `Painted visible group ${{picked.object.userData.group.label}} in viewer`;
    }};

    window.addEventListener('resize', () => {{
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    }});

    fitCamera();
    status.textContent = 'Ready. Full geometry loaded in WebGL.';
    function animate() {{
      controls.update();
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    }}
    animate();
  </script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a local WebGL viewer for a 3MF model.")
    parser.add_argument("input_file", help="Input .3mf file")
    parser.add_argument("--out", help="Output HTML file")
    parser.add_argument("--open", action="store_true", help="Open the generated viewer in the default browser")
    parser.add_argument("--app", action="store_true", help="Open the generated viewer in an application WebView window")
    args = parser.parse_args(argv)

    try:
        if args.app:
            viewer = open_webgl_app_window(args.input_file, args.out)
        else:
            viewer = generate_webgl_viewer(args.input_file, args.out)
            if args.open:
                webbrowser.open(viewer.resolve().as_uri())
        print(viewer)
        return 0
    except SplitterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
