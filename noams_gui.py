#!/usr/bin/env python3
"""Tkinter GUI for the No AMS 3MF splitter."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
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


def remap_split_result_colors(result: SplitResult, color_map: dict[str, str]) -> SplitResult:
    """Return a split result with color labels remapped for preview/export."""

    if not color_map:
        return result
    groups: dict[str, ColorGroup] = {}
    for group in result.groups.values():
        label = color_map.get(group.label, group.label)
        if label not in groups:
            groups[label] = ColorGroup(
                key=label,
                label=label,
                material_id=group.material_id,
                triangles=[],
            )
        groups[label].triangles.extend(group.triangles)
    return SplitResult(
        input_file=result.input_file,
        unit=result.unit,
        groups=groups,
        warnings=result.warnings,
        mesh_files=result.mesh_files,
        total_triangles=result.total_triangles,
        total_vertices=result.total_vertices,
    )


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
        self.last_result: SplitResult | None = None
        self.pending_colors: dict[str, str] = {}
        self.preview_view = tk.StringVar(value="XY")
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
        preview_frame.rowconfigure(1, weight=1)

        preview_tools = ttk.Frame(preview_frame)
        preview_tools.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Radiobutton(preview_tools, text="Top XY", value="XY", variable=self.preview_view, command=self._draw_preview).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Radiobutton(preview_tools, text="Front XZ", value="XZ", variable=self.preview_view, command=self._draw_preview).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        ttk.Radiobutton(preview_tools, text="Side YZ", value="YZ", variable=self.preview_view, command=self._draw_preview).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ttk.Button(preview_tools, text="Fit", command=self._draw_preview).grid(row=0, column=3, sticky="e", padx=(16, 0))

        self.preview_canvas = tk.Canvas(
            preview_frame,
            background="#f4f4f4",
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        self.preview_canvas.grid(row=1, column=0, sticky="nsew")
        self.preview_canvas.bind("<Button-1>", self._select_preview_group)
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
        for index in range(7):
            actions.columnconfigure(index, weight=1)

        self.info_button = ttk.Button(actions, text="Info", command=lambda: self._run_worker(self._load_info))
        self.info_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.split_button = ttk.Button(actions, text="Split", command=lambda: self._run_worker(lambda: self._export(False)))
        self.split_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.zip_button = ttk.Button(actions, text="Split + ZIP", command=lambda: self._run_worker(lambda: self._export(True)))
        self.zip_button.grid(row=0, column=2, sticky="ew", padx=6)
        self.color_button = ttk.Button(actions, text="Change color", command=self._choose_color)
        self.color_button.grid(row=0, column=3, sticky="ew", padx=6)
        self.save_3mf_button = ttk.Button(actions, text="Save painted 3MF", command=self._save_painted_dialog)
        self.save_3mf_button.grid(row=0, column=4, sticky="ew", padx=6)
        ttk.Button(actions, text="Open output", command=self._open_output).grid(row=0, column=5, sticky="ew", padx=6)
        ttk.Button(actions, text="Clear log", command=self._clear_log).grid(row=0, column=6, sticky="ew", padx=(6, 0))

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
        return remap_split_result_colors(result, self.pending_colors)

    def _save_painted_dialog(self) -> None:
        try:
            input_path, _ = self._validate_paths()
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

    def _schedule_preview(self, _event: object | None = None) -> None:
        if self.preview_after_id is not None:
            self.after_cancel(self.preview_after_id)
        self.preview_after_id = self.after(120, self._draw_preview)

    def _draw_preview(self) -> None:
        self.preview_after_id = None
        self.preview_canvas.delete("all")
        result = self.last_result
        if result is None:
            self.preview_canvas.create_text(
                20,
                20,
                anchor="nw",
                text="Load a 3MF file and click Info to preview color groups.",
                fill="#555555",
            )
            return

        width = max(self.preview_canvas.winfo_width(), 200)
        height = max(self.preview_canvas.winfo_height(), 160)
        samples = self._preview_samples(result)
        if not samples:
            self.preview_canvas.create_text(20, 20, anchor="nw", text="No previewable triangles.", fill="#555555")
            return

        projected = [(label, [self._project_point(point) for point in triangle]) for label, triangle in samples]
        xs = [point[0] for _, triangle in projected for point in triangle]
        ys = [point[1] for _, triangle in projected for point in triangle]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        margin = 18
        scale = min((width - margin * 2) / span_x, (height - margin * 2) / span_y)

        selected = self.color_table.selection()
        selected_label = selected[0] if selected else ""
        for label, triangle in projected:
            coords: list[float] = []
            for x, y in triangle:
                coords.extend((margin + (x - min_x) * scale, height - margin - (y - min_y) * scale))
            fill = self._display_color(label)
            outline = "#202020" if label == selected_label else fill
            width_px = 2 if label == selected_label else 1
            self.preview_canvas.create_polygon(
                coords,
                fill=fill,
                outline=outline,
                width=width_px,
                tags=("preview_triangle", f"group:{label}"),
            )

        self.preview_canvas.create_text(
            8,
            height - 8,
            anchor="sw",
            text=f"{self.preview_view.get()} preview, sampled triangles. Click a region to select its color group.",
            fill="#333333",
        )

    def _preview_samples(self, result: SplitResult) -> list[tuple[str, tuple[tuple[float, float, float], ...]]]:
        groups = list(result.groups.values())
        if not groups:
            return []
        max_total = 2200
        max_per_group = max(80, max_total // len(groups))
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
        if view == "XZ":
            return (x, z)
        if view == "YZ":
            return (y, z)
        return (x, y)

    def _display_color(self, label: str) -> str:
        color = self.pending_colors.get(label, label)
        if color.startswith("#") and len(color) == 7:
            return color
        return "#9E9E9E"

    def _select_preview_group(self, event: object) -> None:
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        item = self.preview_canvas.find_closest(x, y)
        if not item:
            return
        tags = self.preview_canvas.gettags(item[0])
        label = next((tag[len("group:") :] for tag in tags if tag.startswith("group:")), "")
        if not label:
            return
        if self.color_table.exists(label):
            self.color_table.selection_set(label)
            self.color_table.focus(label)
            self.color_table.see(label)
            self._draw_preview()

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
