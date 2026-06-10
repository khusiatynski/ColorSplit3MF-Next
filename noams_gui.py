#!/usr/bin/env python3
"""Tkinter GUI for the No AMS 3MF splitter."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from math import cos, radians, sin
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Callable

from noams_painter import recolor_3mf
from noams_splitter import (
    ColorGroup,
    NoAmsSplitter,
    SplitResult,
    SplitterError,
    create_zip_archive,
    export_result,
    result_summary,
    write_color_summary,
    write_report,
)


ScreenTriangle = tuple[tuple[float, float], tuple[float, float], tuple[float, float]]


def remap_split_result_colors(result: SplitResult, color_map: dict[str, str]) -> SplitResult:
    """Return a split result with color labels remapped for preview/export."""

    return apply_preview_edits(result, color_map, {})


def apply_preview_edits(
    result: SplitResult,
    color_map: dict[str, str],
    triangle_colors: dict[int, str],
) -> SplitResult:
    """Return a split result with group and per-triangle preview color edits applied."""

    if not color_map and not triangle_colors:
        return result
    groups: dict[str, ColorGroup] = {}
    triangle_index = 0
    for group in result.groups.values():
        for triangle in group.triangles:
            label = triangle_colors.get(triangle_index, color_map.get(group.label, group.label))
            if label not in groups:
                groups[label] = ColorGroup(
                    key=label,
                    label=label,
                    material_id=group.material_id,
                    triangles=[],
                )
            groups[label].triangles.append(triangle)
            triangle_index += 1
    return SplitResult(
        input_file=result.input_file,
        unit=result.unit,
        groups=groups,
        warnings=result.warnings,
        mesh_files=result.mesh_files,
        total_triangles=result.total_triangles,
        total_vertices=result.total_vertices,
    )


def point_in_screen_triangle(x: float, y: float, triangle: ScreenTriangle) -> bool:
    """Return True when a 2D point lies inside a screen-space triangle."""

    (ax, ay), (bx, by), (cx, cy) = triangle
    denominator = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(denominator) < 1e-12:
        return False
    weight_a = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / denominator
    weight_b = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / denominator
    weight_c = 1.0 - weight_a - weight_b
    epsilon = -1e-9
    return weight_a >= epsilon and weight_b >= epsilon and weight_c >= epsilon


class NoAmsSplitterApp(tk.Tk):
    """Small desktop UI for inspecting and exporting colored 3MF models."""

    def __init__(self) -> None:
        super().__init__()
        self.title("ColorSplit3MF-Next")
        self.geometry("1120x760")
        self.minsize(860, 620)

        self.input_file = tk.StringVar()
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "output"))
        self.status = tk.StringVar(value="Ready")
        self.pick_status = tk.StringVar(value="Picked triangle: none")
        self.last_result: SplitResult | None = None
        self.pending_colors: dict[str, str] = {}
        self.triangle_colors: dict[int, str] = {}
        self.active_paint_color = tk.StringVar(value="#FF0000")
        self.tool_mode = tk.StringVar(value="pick")
        self.preview_view = tk.StringVar(value="3D")
        self.camera_yaw = radians(-35.0)
        self.camera_pitch = radians(28.0)
        self.camera_zoom = 1.0
        self.camera_pan = (0.0, 0.0)
        self.drag_start: tuple[int, int] | None = None
        self.drag_button: int | None = None
        self.drag_moved = False
        self.preview_transform: tuple[int, int, float, float, float, float, float] | None = None
        self.picked_triangle: tuple[str, tuple[tuple[float, float, float], ...], int] | None = None
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.preview_after_id: str | None = None

        self._build_ui()
        self.after(100, self._poll_worker_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(2, weight=3)
        root.rowconfigure(3, weight=1)
        root.rowconfigure(5, weight=1)

        ttk.Label(root, text="3MF file").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(root, textvariable=self.input_file).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse", command=self._browse_input).grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(root, text="Output").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(root, textvariable=self.output_dir).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse", command=self._browse_output).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=4)

        preview_frame = ttk.LabelFrame(root, text="Preview", padding=8)
        preview_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(10, 8))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(2, weight=1)

        preview_tools = ttk.Frame(preview_frame)
        preview_tools.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        preview_tools.columnconfigure(11, weight=1)
        ttk.Label(preview_tools, text="Tool").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(preview_tools, text="Pick", value="pick", variable=self.tool_mode).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Radiobutton(preview_tools, text="Mesh", value="mesh", variable=self.tool_mode).grid(
            row=0, column=2, sticky="w", padx=(8, 0)
        )
        ttk.Radiobutton(preview_tools, text="Triangle", value="triangle", variable=self.tool_mode).grid(
            row=0, column=3, sticky="w", padx=(8, 0)
        )
        self.paint_swatch = tk.Label(preview_tools, textvariable=self.active_paint_color, width=10, relief="solid")
        self.paint_swatch.grid(row=0, column=4, sticky="w", padx=(16, 4))
        self._update_paint_swatch()
        ttk.Button(preview_tools, text="Paint color", command=self._choose_active_paint_color).grid(
            row=0, column=5, sticky="w", padx=(4, 0)
        )
        ttk.Button(preview_tools, text="Paint picked", command=self._paint_picked_triangle).grid(
            row=0, column=6, sticky="w", padx=(8, 0)
        )
        ttk.Button(preview_tools, text="Clear edits", command=self._clear_preview_edits).grid(
            row=0, column=7, sticky="w", padx=(8, 0)
        )

        view_tools = ttk.Frame(preview_frame)
        view_tools.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Radiobutton(
            view_tools,
            text="3D",
            value="3D",
            variable=self.preview_view,
            command=self._draw_preview,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(view_tools, text="Top XY", value="XY", variable=self.preview_view, command=self._draw_preview).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        ttk.Radiobutton(view_tools, text="Front XZ", value="XZ", variable=self.preview_view, command=self._draw_preview).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ttk.Radiobutton(view_tools, text="Side YZ", value="YZ", variable=self.preview_view, command=self._draw_preview).grid(
            row=0, column=3, sticky="w", padx=(10, 0)
        )
        ttk.Button(view_tools, text="Reset camera", command=self._reset_camera).grid(row=0, column=4, sticky="e", padx=(16, 0))
        ttk.Label(view_tools, textvariable=self.pick_status).grid(row=0, column=5, sticky="e", padx=(16, 0))

        self.preview_canvas = tk.Canvas(
            preview_frame,
            background="#f4f4f4",
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        self.preview_canvas.grid(row=2, column=0, sticky="nsew")
        self.preview_canvas.bind("<ButtonPress-1>", self._start_preview_drag)
        self.preview_canvas.bind("<B1-Motion>", self._drag_preview)
        self.preview_canvas.bind("<ButtonRelease-1>", self._end_preview_drag)
        self.preview_canvas.bind("<ButtonPress-3>", self._start_preview_drag)
        self.preview_canvas.bind("<B3-Motion>", self._drag_preview)
        self.preview_canvas.bind("<ButtonRelease-3>", self._end_preview_drag)
        self.preview_canvas.bind("<MouseWheel>", self._zoom_preview)
        self.preview_canvas.bind("<Button-4>", self._zoom_preview)
        self.preview_canvas.bind("<Button-5>", self._zoom_preview)
        self.preview_canvas.bind("<Configure>", self._schedule_preview)

        table_frame = ttk.LabelFrame(root, text="Detected colors", padding=8)
        table_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(0, 8))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("color", "new_color", "material", "triangles")
        self.color_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        self.color_table.heading("color", text="Color / group")
        self.color_table.heading("new_color", text="New color")
        self.color_table.heading("material", text="Material ID")
        self.color_table.heading("triangles", text="Triangles")
        self.color_table.column("color", width=180, anchor="w")
        self.color_table.column("new_color", width=140, anchor="w")
        self.color_table.column("material", width=160, anchor="w")
        self.color_table.column("triangles", width=120, anchor="e")
        self.color_table.grid(row=0, column=0, sticky="nsew")

        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.color_table.yview)
        self.color_table.configure(yscrollcommand=table_scroll.set)
        table_scroll.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(root)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        for index in range(8):
            actions.columnconfigure(index, weight=1)

        self.info_button = ttk.Button(actions, text="Info", command=lambda: self._run_worker(self._load_info))
        self.info_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.split_button = ttk.Button(actions, text="Split", command=lambda: self._run_worker(lambda: self._export(False)))
        self.split_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.zip_button = ttk.Button(actions, text="Split + ZIP", command=lambda: self._run_worker(lambda: self._export(True)))
        self.zip_button.grid(row=0, column=2, sticky="ew", padx=6)
        ttk.Button(actions, text="3D Engine", command=self._open_webgl_engine).grid(row=0, column=3, sticky="ew", padx=6)
        self.color_button = ttk.Button(actions, text="Change color", command=self._choose_color)
        self.color_button.grid(row=0, column=4, sticky="ew", padx=6)
        self.save_3mf_button = ttk.Button(actions, text="Save painted 3MF", command=self._save_painted_dialog)
        self.save_3mf_button.grid(row=0, column=5, sticky="ew", padx=6)
        ttk.Button(actions, text="Open output", command=self._open_output).grid(row=0, column=6, sticky="ew", padx=6)
        ttk.Button(actions, text="Clear log", command=self._clear_log).grid(row=0, column=7, sticky="ew", padx=(6, 0))

        log_frame = ttk.LabelFrame(root, text="Log", padding=8)
        log_frame.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

        ttk.Label(root, textvariable=self.status).grid(row=6, column=0, columnspan=3, sticky="w")

    def _browse_input(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select 3MF model",
            filetypes=(("3MF files", "*.3mf"), ("All files", "*.*")),
        )
        if filename:
            self.input_file.set(filename)
            if self.output_dir.get() == str(Path.cwd() / "output"):
                self.output_dir.set(str(Path(filename).with_suffix("")) + "_split")

    def _browse_output(self) -> None:
        directory = filedialog.askdirectory(title="Select output directory")
        if directory:
            self.output_dir.set(directory)

    def _validate_paths(self) -> tuple[Path, Path]:
        raw_input = self.input_file.get().strip()
        raw_output = self.output_dir.get().strip()
        if not raw_input:
            raise SplitterError("Select a .3mf input file first.")
        if not raw_output:
            raise SplitterError("Select an output directory first.")
        return Path(raw_input), Path(raw_output)

    def _load_info(self) -> str:
        input_path, _ = self._validate_paths()
        result = NoAmsSplitter(input_path).split()
        self.last_result = result
        self.pending_colors.clear()
        self.triangle_colors.clear()
        self.picked_triangle = None
        self.pick_status.set("Picked triangle: none")
        return self._format_result(result)

    def _export(self, with_zip: bool) -> str:
        input_path, output_path = self._validate_paths()
        result = NoAmsSplitter(input_path).split()
        result = self._result_with_pending_colors(result)
        exported_files = export_result(result, output_path, "stl")
        report_file = output_path / "export_report.json"
        summary_file = output_path / "color_summary.txt"
        diagnostic_file = output_path / "diagnostic_report.json"
        write_report(report_file, result, exported_files)
        write_color_summary(summary_file, result)
        write_report(diagnostic_file, result, exported_files)

        zip_line = ""
        if with_zip:
            zip_path = create_zip_archive(output_path, exported_files, report_file, summary_file)
            zip_line = f"\nZIP: {zip_path}"

        self.last_result = result
        return (
            f"{self._format_result(result)}\n"
            f"Exported STL files: {len(exported_files)}\n"
            f"Output: {output_path}{zip_line}"
        )

    def _result_with_pending_colors(self, result: SplitResult) -> SplitResult:
        return apply_preview_edits(result, self.pending_colors, self.triangle_colors)

    def _save_painted_dialog(self) -> None:
        try:
            input_path, _ = self._validate_paths()
            if self.triangle_colors:
                raise SplitterError("Saving per-triangle edits to 3MF is not implemented yet. Use Split/STL export for now.")
            if not self.pending_colors:
                raise SplitterError("No color changes selected.")

            output_path = filedialog.asksaveasfilename(
                title="Save painted 3MF",
                defaultextension=".3mf",
                initialfile=f"{input_path.stem}_painted.3mf",
                filetypes=(("3MF files", "*.3mf"), ("All files", "*.*")),
            )
            if not output_path:
                return

            self._set_busy(True)
            self.status.set("Saving painted 3MF...")
            changed = recolor_3mf(input_path, output_path, self.pending_colors)
            result = NoAmsSplitter(output_path).split()
            self.input_file.set(output_path)
            self.last_result = result
            self.pending_colors.clear()
            self.triangle_colors.clear()
            self.picked_triangle = None
            self.pick_status.set("Picked triangle: none")
            self._refresh_table()
            self._draw_preview()
            self._append_log(
                f"Saved painted 3MF: {output_path}\nChanged color definitions: {changed}\n\n{self._format_result(result)}"
            )
            self.status.set("Ready")
        except Exception as exc:
            self._append_log(f"Error: {exc}")
            self.status.set("Error")
            messagebox.showerror("ColorSplit3MF-Next", str(exc))
        finally:
            self._set_busy(False)

    def _format_result(self, result: SplitResult) -> str:
        summary = result_summary(result)
        lines = [
            f"Input: {summary['input']}",
            f"Unit: {summary['unit']}",
            f"Detected colors: {summary['detected_colors']}",
            f"Total triangles: {summary['total_triangles']}",
            f"Total vertices: {summary['total_vertices']}",
        ]
        for color in summary["colors"]:
            lines.append(f"{color['label']}: {color['triangles']} triangles ({color['material_id']})")
        for warning in summary["warnings"]:
            lines.append(f"Warning: {warning}")
        return "\n".join(lines)

    def _run_worker(self, task: Callable[[], str]) -> None:
        self._set_busy(True)
        self.status.set("Working...")

        def worker() -> None:
            try:
                self.worker_queue.put(("success", task()))
            except Exception as exc:  # UI boundary: show parser and filesystem errors to the user.
                self.worker_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_worker_queue(self) -> None:
        try:
            kind, payload = self.worker_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_worker_queue)
            return

        self._set_busy(False)
        if kind == "success":
            self._refresh_table()
            self._draw_preview()
            self._append_log(str(payload))
            self.status.set("Ready")
        else:
            self._append_log(f"Error: {payload}")
            self.status.set("Error")
            messagebox.showerror("ColorSplit3MF-Next", str(payload))
        self.after(100, self._poll_worker_queue)

    def _refresh_table(self) -> None:
        self.color_table.delete(*self.color_table.get_children())
        if self.last_result is None:
            return
        for group in self.last_result.groups.values():
            new_color = self.pending_colors.get(group.label, "")
            self.color_table.insert(
                "",
                "end",
                iid=group.label,
                values=(group.label, new_color, group.material_id, group.triangle_count),
            )

    def _choose_color(self) -> None:
        selection = self.color_table.selection()
        if not selection:
            messagebox.showinfo("ColorSplit3MF-Next", "Select a color group first.")
            return
        old_color = selection[0]
        if not old_color.startswith("#"):
            messagebox.showinfo("ColorSplit3MF-Next", "This MVP can recolor HEX color groups only.")
            return
        _, selected = colorchooser.askcolor(color=old_color, title="Choose new color")
        if not selected:
            return
        self.pending_colors[old_color] = selected.upper()
        self._refresh_table()
        self._draw_preview()
        self._append_log(f"Pending color change: {old_color} -> {selected.upper()}")

    def _choose_active_paint_color(self) -> None:
        _, selected = colorchooser.askcolor(color=self.active_paint_color.get(), title="Choose paint color")
        if not selected:
            return
        self.active_paint_color.set(selected.upper())
        self._update_paint_swatch()

    def _update_paint_swatch(self) -> None:
        color = self.active_paint_color.get()
        text_color = "#FFFFFF" if self._relative_luma(color) < 0.45 else "#111111"
        self.paint_swatch.configure(background=color, foreground=text_color)

    def _relative_luma(self, color: str) -> float:
        if not color.startswith("#") or len(color) != 7:
            return 1.0
        red = int(color[1:3], 16) / 255.0
        green = int(color[3:5], 16) / 255.0
        blue = int(color[5:7], 16) / 255.0
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    def _paint_picked_triangle(self) -> None:
        if self.picked_triangle is None:
            messagebox.showinfo("ColorSplit3MF-Next", "Pick a triangle first.")
            return
        label, _triangle, triangle_index = self.picked_triangle
        color = self.active_paint_color.get().upper()
        if self.tool_mode.get() == "mesh":
            self.pending_colors[label] = color
            self._append_log(f"Painted color group {label} -> {color}")
        else:
            self.triangle_colors[triangle_index] = color
            self._append_log(f"Painted triangle {triangle_index} -> {color}")
        self.picked_triangle = None
        self.pick_status.set("Picked triangle: none")
        self._refresh_table()
        self._draw_preview()

    def _clear_preview_edits(self) -> None:
        self.pending_colors.clear()
        self.triangle_colors.clear()
        self.picked_triangle = None
        self.pick_status.set("Picked triangle: none")
        self._refresh_table()
        self._draw_preview()
        self._append_log("Cleared preview color edits.")

    def _open_webgl_engine(self) -> None:
        try:
            input_path, _ = self._validate_paths()
            import webview  # noqa: F401  # type: ignore[import-not-found]
        except ImportError:
            messagebox.showerror(
                "ColorSplit3MF-Next",
                "The application WebGL engine requires pywebview.\n\nInstall it with:\npython -m pip install -e .[viewer]",
            )
            return
        except Exception as exc:
            messagebox.showerror("ColorSplit3MF-Next", str(exc))
            return

        command = [sys.executable, str(Path(__file__).with_name("noams_web_viewer.py")), str(input_path), "--app"]
        subprocess.Popen(command, cwd=str(Path(__file__).parent))
        self._append_log("Opened WebGL 3D engine window.")

    def _schedule_preview(self, _event: object | None = None) -> None:
        if self.preview_after_id is not None:
            self.after_cancel(self.preview_after_id)
        self.preview_after_id = self.after(120, self._draw_preview)

    def _reset_camera(self) -> None:
        self.preview_view.set("3D")
        self.camera_yaw = radians(-35.0)
        self.camera_pitch = radians(28.0)
        self.camera_zoom = 1.0
        self.camera_pan = (0.0, 0.0)
        self._draw_preview()

    def _start_preview_drag(self, event: object) -> None:
        self.drag_start = (int(getattr(event, "x", 0)), int(getattr(event, "y", 0)))
        self.drag_button = int(getattr(event, "num", 1))
        self.drag_moved = False

    def _drag_preview(self, event: object) -> None:
        if self.drag_start is None:
            return
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        last_x, last_y = self.drag_start
        dx = x - last_x
        dy = y - last_y
        if abs(dx) + abs(dy) > 2:
            self.drag_moved = True
        if self.drag_button == 3:
            pan_x, pan_y = self.camera_pan
            self.camera_pan = (pan_x + dx, pan_y + dy)
        else:
            self.preview_view.set("3D")
            self.camera_yaw += dx * 0.012
            self.camera_pitch = max(radians(-82.0), min(radians(82.0), self.camera_pitch + dy * 0.012))
        self.drag_start = (x, y)
        self._draw_preview()

    def _end_preview_drag(self, event: object) -> None:
        if self.drag_button == 1 and not self.drag_moved:
            self._select_preview_group(event)
        self.drag_start = None
        self.drag_button = None
        self.drag_moved = False

    def _zoom_preview(self, event: object) -> None:
        delta = int(getattr(event, "delta", 0))
        button = int(getattr(event, "num", 0))
        if delta > 0 or button == 4:
            self.camera_zoom *= 1.12
        elif delta < 0 or button == 5:
            self.camera_zoom /= 1.12
        self.camera_zoom = max(0.25, min(8.0, self.camera_zoom))
        self._draw_preview()

    def _draw_preview(self) -> None:
        self.preview_after_id = None
        self.preview_transform = None
        self.preview_canvas.delete("all")
        source_result = self.last_result
        if source_result is None:
            self.preview_canvas.create_text(
                20,
                20,
                anchor="nw",
                text="Load a 3MF file and click Info to preview color groups.",
                fill="#555555",
            )
            return
        result = self._result_with_pending_colors(source_result)

        width = max(self.preview_canvas.winfo_width(), 200)
        height = max(self.preview_canvas.winfo_height(), 160)
        samples = self._preview_samples(result)
        if not samples:
            self.preview_canvas.create_text(20, 20, anchor="nw", text="No previewable triangles.", fill="#555555")
            return

        projected = [
            (
                label,
                [self._project_point(point) for point in triangle],
                sum(self._project_depth(point) for point in triangle) / 3.0,
                self._triangle_shade(triangle),
            )
            for label, triangle in samples
        ]
        xs = [point[0] for _, triangle, _depth, _shade in projected for point in triangle]
        ys = [point[1] for _, triangle, _depth, _shade in projected for point in triangle]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        margin = 18
        scale = min((width - margin * 2) / span_x, (height - margin * 2) / span_y)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        pan_x, pan_y = self.camera_pan
        self.preview_transform = (width, height, center_x, center_y, scale, pan_x, pan_y)

        self._draw_preview_background(width, height)

        selected = self.color_table.selection()
        selected_label = selected[0] if selected else ""
        for label, triangle, _depth, shade in sorted(projected, key=lambda item: item[2]):
            coords: list[float] = []
            for x, y in triangle:
                coords.extend(self._projected_to_screen(x, y))
            fill = self._display_color(label, shade)
            outline = "#1f1f1f" if label == selected_label else self._display_color(label, max(shade - 0.18, 0.35))
            width_px = 2 if label == selected_label else 1
            self.preview_canvas.create_polygon(
                coords,
                fill=fill,
                outline=outline,
                width=width_px,
                tags=("preview_triangle", f"group:{label}"),
            )

        if self.picked_triangle is not None:
            label, triangle, _index = self.picked_triangle
            coords = []
            for point in triangle:
                x, y = self._project_point(point)
                coords.extend(self._projected_to_screen(x, y))
            self.preview_canvas.create_polygon(coords, fill="", outline="#FF2D00", width=3)
            self.preview_canvas.create_oval(coords[0] - 3, coords[1] - 3, coords[0] + 3, coords[1] + 3, fill="#FF2D00", outline="")

        self.preview_canvas.create_text(
            8,
            height - 8,
            anchor="sw",
            text=(
                f"{self.preview_view.get()} engine, sampled triangles. "
                "Left drag rotates, wheel zooms, right drag pans, click picks the real triangle under the cursor."
            ),
            fill="#333333",
        )

    def _preview_samples(self, result: SplitResult) -> list[tuple[str, tuple[tuple[float, float, float], ...]]]:
        groups = list(result.groups.values())
        if not groups:
            return []
        max_total = 5200
        max_per_group = max(220, max_total // len(groups))
        samples: list[tuple[str, tuple[tuple[float, float, float], ...]]] = []
        for group in groups:
            triangles = group.triangles
            if not triangles:
                continue
            step = max(1, len(triangles) // max_per_group)
            for triangle in triangles[::step][:max_per_group]:
                samples.append((group.label, triangle))
        return samples

    def _project_point(self, point: tuple[float, float, float]) -> tuple[float, float]:
        x, y, z = point
        view = self.preview_view.get()
        if view == "3D":
            tx, ty, _tz = self._camera_point(point)
            return (tx, ty)
        if view == "XZ":
            return (x, z)
        if view == "YZ":
            return (y, z)
        return (x, y)

    def _projected_to_screen(self, x: float, y: float) -> tuple[float, float]:
        if self.preview_transform is None:
            return (x, y)
        width, height, center_x, center_y, scale, pan_x, pan_y = self.preview_transform
        return (
            width / 2.0 + (x - center_x) * scale * self.camera_zoom + pan_x,
            height / 2.0 - (y - center_y) * scale * self.camera_zoom + pan_y,
        )

    def _project_depth(self, point: tuple[float, float, float]) -> float:
        x, y, z = point
        view = self.preview_view.get()
        if view == "3D":
            return self._camera_point(point)[2]
        if view == "XZ":
            return y
        if view == "YZ":
            return x
        return z

    def _camera_point(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = point
        yaw_cos = cos(self.camera_yaw)
        yaw_sin = sin(self.camera_yaw)
        pitch_cos = cos(self.camera_pitch)
        pitch_sin = sin(self.camera_pitch)

        x1 = x * yaw_cos - y * yaw_sin
        y1 = x * yaw_sin + y * yaw_cos
        z1 = z

        y2 = y1 * pitch_cos - z1 * pitch_sin
        z2 = y1 * pitch_sin + z1 * pitch_cos
        return (x1, y2, z2)

    def _triangle_shade(self, triangle: tuple[tuple[float, float, float], ...]) -> float:
        ax, ay, az = triangle[0]
        bx, by, bz = triangle[1]
        cx, cy, cz = triangle[2]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        if length == 0:
            return 0.8
        nx, ny, nz = self._camera_vector((nx / length, ny / length, nz / length))
        light = (-0.35, -0.45, 0.82)
        dot = abs(nx * light[0] + ny * light[1] + nz * light[2])
        return 0.56 + dot * 0.44

    def _camera_vector(self, vector: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = vector
        yaw_cos = cos(self.camera_yaw)
        yaw_sin = sin(self.camera_yaw)
        pitch_cos = cos(self.camera_pitch)
        pitch_sin = sin(self.camera_pitch)
        x1 = x * yaw_cos - y * yaw_sin
        y1 = x * yaw_sin + y * yaw_cos
        z1 = z
        return (x1, y1 * pitch_cos - z1 * pitch_sin, y1 * pitch_sin + z1 * pitch_cos)

    def _display_color(self, label: str, shade: float = 1.0) -> str:
        color = self.pending_colors.get(label, label)
        if color.startswith("#") and len(color) == 7:
            red = int(color[1:3], 16)
            green = int(color[3:5], 16)
            blue = int(color[5:7], 16)
            shade = max(0.25, min(shade, 1.15))
            return (
                f"#{min(int(red * shade), 255):02X}"
                f"{min(int(green * shade), 255):02X}"
                f"{min(int(blue * shade), 255):02X}"
            )
        return "#9E9E9E"

    def _draw_preview_background(self, width: int, height: int) -> None:
        self.preview_canvas.create_rectangle(0, 0, width, height, fill="#f7f8f9", outline="")
        step = 40
        for x in range(0, width + step, step):
            self.preview_canvas.create_line(x, 0, x, height, fill="#eceff1")
        for y in range(0, height + step, step):
            self.preview_canvas.create_line(0, y, width, y, fill="#eceff1")

    def _select_preview_group(self, event: object) -> None:
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        picked = self._pick_triangle_at(x, y)
        if picked is None:
            self.pick_status.set("Picked triangle: none")
            return
        label, triangle, triangle_index, _depth = picked
        self.picked_triangle = (label, triangle, triangle_index)
        self.pick_status.set(f"Picked triangle: {triangle_index} ({label})")
        self._append_log(f"Picked triangle {triangle_index} in group {label}")
        if self.color_table.exists(label):
            self.color_table.selection_set(label)
            self.color_table.focus(label)
            self.color_table.see(label)
        if self.tool_mode.get() in {"mesh", "triangle"}:
            self._paint_picked_triangle()
            return
        self._draw_preview()

    def _pick_triangle_at(
        self,
        screen_x: float,
        screen_y: float,
    ) -> tuple[str, tuple[tuple[float, float, float], ...], int, float] | None:
        result = self.last_result
        if result is None or self.preview_transform is None:
            return None

        best: tuple[str, tuple[tuple[float, float, float], ...], int, float] | None = None
        triangle_index = 0
        for group in result.groups.values():
            for triangle in group.triangles:
                projected = [self._project_point(point) for point in triangle]
                screen_triangle: ScreenTriangle = tuple(self._projected_to_screen(x, y) for x, y in projected)  # type: ignore[assignment]
                min_x = min(point[0] for point in screen_triangle)
                max_x = max(point[0] for point in screen_triangle)
                min_y = min(point[1] for point in screen_triangle)
                max_y = max(point[1] for point in screen_triangle)
                if screen_x < min_x or screen_x > max_x or screen_y < min_y or screen_y > max_y:
                    triangle_index += 1
                    continue
                if not point_in_screen_triangle(screen_x, screen_y, screen_triangle):
                    triangle_index += 1
                    continue
                depth = sum(self._project_depth(point) for point in triangle) / 3.0
                if best is None or depth > best[3]:
                    best = (group.label, triangle, triangle_index, depth)
                triangle_index += 1
        return best

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        if self.log_text.get("1.0", "end-1c"):
            self.log_text.insert("end", "\n\n")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.info_button.configure(state=state)
        self.split_button.configure(state=state)
        self.zip_button.configure(state=state)
        self.color_button.configure(state=state)
        self.save_3mf_button.configure(state=state)

    def _open_output(self) -> None:
        output_path = Path(self.output_dir.get().strip())
        if not output_path.exists():
            messagebox.showinfo("ColorSplit3MF-Next", "Output directory does not exist yet.")
            return
        os.startfile(output_path)  # type: ignore[attr-defined]


def main() -> None:
    app = NoAmsSplitterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
