#!/usr/bin/env python3
"""Tkinter GUI for the No AMS 3MF splitter."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from noams_splitter import (
    NoAmsSplitter,
    SplitResult,
    SplitterError,
    create_zip_archive,
    export_result,
    result_summary,
    write_color_summary,
    write_report,
)


class NoAmsSplitterApp(tk.Tk):
    """Small desktop UI for inspecting and exporting colored 3MF models."""

    def __init__(self) -> None:
        super().__init__()
        self.title("ColorSplit3MF-Next")
        self.geometry("920x620")
        self.minsize(760, 520)

        self.input_file = tk.StringVar()
        self.output_dir = tk.StringVar(value=str(Path.cwd() / "output"))
        self.status = tk.StringVar(value="Ready")
        self.last_result: SplitResult | None = None
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self._build_ui()
        self.after(100, self._poll_worker_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(2, weight=1)
        root.rowconfigure(4, weight=1)

        ttk.Label(root, text="3MF file").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(root, textvariable=self.input_file).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse", command=self._browse_input).grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(root, text="Output").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(root, textvariable=self.output_dir).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse", command=self._browse_output).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=4)

        table_frame = ttk.LabelFrame(root, text="Detected colors", padding=8)
        table_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(10, 8))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("color", "material", "triangles")
        self.color_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        self.color_table.heading("color", text="Color / group")
        self.color_table.heading("material", text="Material ID")
        self.color_table.heading("triangles", text="Triangles")
        self.color_table.column("color", width=220, anchor="w")
        self.color_table.column("material", width=160, anchor="w")
        self.color_table.column("triangles", width=120, anchor="e")
        self.color_table.grid(row=0, column=0, sticky="nsew")

        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.color_table.yview)
        self.color_table.configure(yscrollcommand=table_scroll.set)
        table_scroll.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(root)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
        for index in range(5):
            actions.columnconfigure(index, weight=1)

        self.info_button = ttk.Button(actions, text="Info", command=lambda: self._run_worker(self._load_info))
        self.info_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.split_button = ttk.Button(actions, text="Split", command=lambda: self._run_worker(lambda: self._export(False)))
        self.split_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.zip_button = ttk.Button(actions, text="Split + ZIP", command=lambda: self._run_worker(lambda: self._export(True)))
        self.zip_button.grid(row=0, column=2, sticky="ew", padx=6)
        ttk.Button(actions, text="Open output", command=self._open_output).grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Button(actions, text="Clear log", command=self._clear_log).grid(row=0, column=4, sticky="ew", padx=(6, 0))

        log_frame = ttk.LabelFrame(root, text="Log", padding=8)
        log_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

        ttk.Label(root, textvariable=self.status).grid(row=5, column=0, columnspan=3, sticky="w")

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
        return self._format_result(result)

    def _export(self, with_zip: bool) -> str:
        input_path, output_path = self._validate_paths()
        result = NoAmsSplitter(input_path).split()
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
            self.color_table.insert("", "end", values=(group.label, group.material_id, group.triangle_count))

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
