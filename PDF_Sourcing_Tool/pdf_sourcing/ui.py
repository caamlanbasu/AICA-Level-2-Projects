"""CustomTkinter user interface.

All network and PDF work runs in ``SourcingWorker``. The window only reads
events from a queue (every 100 ms), so it stays responsive during a run.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

from .config import APP_NAME, APP_VERSION, NOT_FOUND_TEXT
from .excel_io import ExcelUrlList, UrlRow
from .models import (
    ExcelFormatError,
    FinishedEvent,
    Job,
    LogEvent,
    ProgressEvent,
    RowEvent,
    SaveBlockedEvent,
)
from .pipeline import STATUS_QUEUED, SourcingWorker, build_jobs
from .text_utils import normalise_url

# --------------------------------------------------------------------------- #
# Look and feel
# --------------------------------------------------------------------------- #
MODE_SINGLE = "🔗 Single URL"
MODE_EXCEL = "📊 Excel list"

POLL_INTERVAL_MS = 100
MAX_EVENTS_PER_POLL = 200
MAX_LOG_LINES = 3000

# (light, dark) colour pairs
COLOR_WINDOW = ("#EEF2F8", "#0F141C")
COLOR_CARD = ("#FFFFFF", "#18202C")
COLOR_HEADER = ("#16324F", "#101E30")
COLOR_HEADER_TEXT = "#FFFFFF"
COLOR_HEADER_SUB = "#F5C453"
COLOR_TEXT = ("#1B2733", "#E6ECF4")
COLOR_MUTED = ("#5B6B7C", "#93A1B3")
COLOR_FIELD = ("#F5F8FC", "#111822")
COLOR_FIELD_BORDER = ("#C9D4E3", "#2C3A4D")

COLOR_START = ("#12996B", "#12996B")
COLOR_START_HOVER = ("#0E7F59", "#0E7F59")
COLOR_STOP = ("#D9434E", "#D9434E")
COLOR_STOP_HOVER = ("#B8323C", "#B8323C")
COLOR_FOLDER = ("#2E7DD1", "#2E7DD1")
COLOR_FOLDER_HOVER = ("#2466AD", "#2466AD")
COLOR_BROWSE = ("#7A52CC", "#7A52CC")
COLOR_BROWSE_HOVER = ("#643FB0", "#643FB0")
COLOR_OUTPUT = ("#D9820F", "#D9820F")
COLOR_OUTPUT_HOVER = ("#B86C08", "#B86C08")
COLOR_PROGRESS = ("#12996B", "#2BC48F")

TABLE_COLORS = {
    "Light": {
        "bg": "#FFFFFF", "alt": "#F4F7FB", "fg": "#1B2733", "head_bg": "#16324F", "head_fg": "#FFFFFF",
        "select": "#CFE3FA", "ok": "#0E7F59", "fail": "#C0303B", "working": "#2466AD", "muted": "#6B7A8C",
    },
    "Dark": {
        "bg": "#18202C", "alt": "#1D2735", "fg": "#E6ECF4", "head_bg": "#22344D", "head_fg": "#FFFFFF",
        "select": "#2C4666", "ok": "#3DD6A0", "fail": "#FF7B85", "working": "#7DB7F5", "muted": "#93A1B3",
    },
}


class App(ctk.CTk):
    """Main window of PDF Sourcing Tool."""

    def __init__(self) -> None:
        super().__init__()

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1120x780")
        self.minsize(960, 660)
        self.configure(fg_color=COLOR_WINDOW)

        self.events: "queue.Queue[object]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[SourcingWorker] = None
        self.excel_path: Optional[Path] = None
        self.output_dir: Optional[Path] = None
        self.row_ids: Dict[int, str] = {}
        self._closing = False

        self.mode_var = tk.StringVar(value=MODE_SINGLE)
        self.excel_var = tk.StringVar(value="No workbook selected")
        self.output_var = tk.StringVar(value="No folder selected")
        self.counter_var = tk.StringVar(value="0 of 0")
        self.phase_var = tk.StringVar(value="Ready")

        self._build_header()
        self._build_inputs()
        self._build_actions()
        self._build_results()
        self._build_log()
        self._style_table()
        self._on_mode_change(MODE_SINGLE)
        self._refresh_controls()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=3)
        self.grid_rowconfigure(4, weight=2)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_INTERVAL_MS, self._poll_events)

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=COLOR_HEADER, corner_radius=0, height=76)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w", padx=22, pady=14)
        ctk.CTkLabel(
            titles, text=f"📑 {APP_NAME}", text_color=COLOR_HEADER_TEXT,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            titles, text="Finds each company's latest financial report and reads its Balance Sheet date",
            text_color=COLOR_HEADER_SUB, font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, sticky="w")

        theme = ctk.CTkFrame(header, fg_color="transparent")
        theme.grid(row=0, column=1, sticky="e", padx=22)
        ctk.CTkLabel(theme, text="🎨 Theme", text_color=COLOR_HEADER_TEXT, font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, padx=(0, 8)
        )
        self.theme_menu = ctk.CTkOptionMenu(
            theme, values=["System", "Light", "Dark"], width=110, command=self._on_theme_change,
            fg_color="#2B5078", button_color="#234465", button_hover_color="#1B3650",
        )
        self.theme_menu.set("System")
        self.theme_menu.grid(row=0, column=1)

    def _build_inputs(self) -> None:
        card = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=14)
        card.grid(row=1, column=0, sticky="ew", padx=18, pady=(16, 8))
        card.grid_columnconfigure(1, weight=1)
        label_font = ctk.CTkFont(size=14, weight="bold")

        ctk.CTkLabel(card, text="🧭 Input", font=label_font, text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", padx=(18, 12), pady=(16, 8)
        )
        self.mode_switch = ctk.CTkSegmentedButton(
            card, values=[MODE_SINGLE, MODE_EXCEL], variable=self.mode_var, command=self._on_mode_change,
            font=ctk.CTkFont(size=13, weight="bold"), height=34,
            selected_color="#16324F", selected_hover_color="#1F456B",
        )
        self.mode_switch.grid(row=0, column=1, sticky="w", pady=(16, 8))

        # Single URL row
        self.url_label = ctk.CTkLabel(card, text="🔗 URL", font=label_font, text_color=COLOR_TEXT)
        self.url_entry = ctk.CTkEntry(
            card, placeholder_text="Company website, for example www.example.com", height=38,
            fg_color=COLOR_FIELD, border_color=COLOR_FIELD_BORDER, text_color=COLOR_TEXT,
        )
        self.url_entry.bind("<Return>", lambda _event: self._on_start())

        # Excel row
        self.excel_label = ctk.CTkLabel(card, text="📊 Excel file", font=label_font, text_color=COLOR_TEXT)
        self.excel_entry = ctk.CTkEntry(
            card, textvariable=self.excel_var, state="readonly", height=38,
            fg_color=COLOR_FIELD, border_color=COLOR_FIELD_BORDER, text_color=COLOR_MUTED,
        )
        self.browse_button = ctk.CTkButton(
            card, text="📂 Browse Excel", width=170, height=38, command=self._on_browse_excel,
            fg_color=COLOR_BROWSE, hover_color=COLOR_BROWSE_HOVER, font=ctk.CTkFont(size=13, weight="bold"),
        )

        # Output folder row
        ctk.CTkLabel(card, text="💾 Save PDFs to", font=label_font, text_color=COLOR_TEXT).grid(
            row=2, column=0, sticky="w", padx=(18, 12), pady=(8, 16)
        )
        self.output_entry = ctk.CTkEntry(
            card, textvariable=self.output_var, state="readonly", height=38,
            fg_color=COLOR_FIELD, border_color=COLOR_FIELD_BORDER, text_color=COLOR_MUTED,
        )
        self.output_entry.grid(row=2, column=1, sticky="ew", pady=(8, 16))
        self.output_button = ctk.CTkButton(
            card, text="💾 Output Folder", width=170, height=38, command=self._on_choose_output,
            fg_color=COLOR_OUTPUT, hover_color=COLOR_OUTPUT_HOVER, font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.output_button.grid(row=2, column=2, padx=(10, 18), pady=(8, 16))

    def _build_actions(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 8))
        bar.grid_columnconfigure(3, weight=1)
        button_font = ctk.CTkFont(size=14, weight="bold")

        self.start_button = ctk.CTkButton(
            bar, text="🚀 Start", width=150, height=42, command=self._on_start, font=button_font,
            fg_color=COLOR_START, hover_color=COLOR_START_HOVER,
        )
        self.start_button.grid(row=0, column=0, padx=(0, 10))
        self.stop_button = ctk.CTkButton(
            bar, text="⏹ Stop", width=130, height=42, command=self._on_stop, font=button_font,
            fg_color=COLOR_STOP, hover_color=COLOR_STOP_HOVER,
        )
        self.stop_button.grid(row=0, column=1, padx=(0, 10))
        self.folder_button = ctk.CTkButton(
            bar, text="📁 Open Folder", width=160, height=42, command=self._on_open_folder, font=button_font,
            fg_color=COLOR_FOLDER, hover_color=COLOR_FOLDER_HOVER,
        )
        self.folder_button.grid(row=0, column=2)

        status = ctk.CTkFrame(bar, fg_color="transparent")
        status.grid(row=0, column=3, sticky="ew", padx=(18, 0))
        status.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(status, textvariable=self.phase_var, text_color=COLOR_MUTED, anchor="w",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(status, textvariable=self.counter_var, text_color=COLOR_TEXT, anchor="e",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=1, sticky="e")
        self.progress = ctk.CTkProgressBar(status, height=12, progress_color=COLOR_PROGRESS)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.progress.set(0)

    def _build_results(self) -> None:
        card = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=14)
        card.grid(row=3, column=0, sticky="nsew", padx=18, pady=8)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(card, text="📋 Results", font=ctk.CTkFont(size=14, weight="bold"), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", padx=18, pady=(12, 6)
        )

        holder = ctk.CTkFrame(card, fg_color="transparent")
        holder.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)

        columns = ("url", "status", "ped", "comments")
        self.table = ttk.Treeview(holder, columns=columns, show="headings", style="Results.Treeview",
                                  selectmode="browse")
        self.table.heading("url", text="URL", anchor="w")
        self.table.heading("status", text="Status", anchor="w")
        self.table.heading("ped", text="Latest PED", anchor="w")
        self.table.heading("comments", text="Comments", anchor="w")
        self.table.column("url", width=420, minwidth=220, anchor="w", stretch=True)
        self.table.column("status", width=190, minwidth=140, anchor="w", stretch=False)
        self.table.column("ped", width=170, minwidth=120, anchor="w", stretch=False)
        self.table.column("comments", width=230, minwidth=140, anchor="w", stretch=True)
        self.table.grid(row=0, column=0, sticky="nsew")

        scrollbar = ctk.CTkScrollbar(holder, command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.table.configure(yscrollcommand=scrollbar.set)

    def _build_log(self) -> None:
        card = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=14)
        card.grid(row=4, column=0, sticky="nsew", padx=18, pady=(8, 18))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(card, text="📝 Live log", font=ctk.CTkFont(size=14, weight="bold"), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", padx=18, pady=(12, 6)
        )
        self.log_box = ctk.CTkTextbox(
            card, wrap="word", state="disabled", fg_color=COLOR_FIELD, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_FIELD_BORDER, font=ctk.CTkFont(size=12),
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

    def _style_table(self) -> None:
        """Colour the ttk table to match the current light / dark appearance."""
        palette = TABLE_COLORS["Dark" if ctk.get_appearance_mode() == "Dark" else "Light"]
        style = ttk.Style(self)
        style.theme_use("clam")
        body_font = tkfont.nametofont("TkDefaultFont").copy()
        body_font.configure(size=11)
        head_font = body_font.copy()
        head_font.configure(weight="bold")
        self._table_fonts = (body_font, head_font)  # keep references alive
        row_height = body_font.metrics("linespace") + 14

        style.configure(
            "Results.Treeview", background=palette["bg"], fieldbackground=palette["bg"],
            foreground=palette["fg"], rowheight=row_height, borderwidth=0, font=body_font,
        )
        style.configure(
            "Results.Treeview.Heading", background=palette["head_bg"], foreground=palette["head_fg"],
            relief="flat", padding=(8, 8), font=head_font,
        )
        style.map("Results.Treeview.Heading", background=[("active", palette["head_bg"])])
        style.map(
            "Results.Treeview",
            background=[("selected", palette["select"])],
            foreground=[("selected", palette["fg"])],
        )
        self.table.tag_configure("pending", foreground=palette["muted"])
        self.table.tag_configure("working", foreground=palette["working"])
        self.table.tag_configure("ok", foreground=palette["ok"])
        self.table.tag_configure("fail", foreground=palette["fail"])
        self.table.tag_configure("cancelled", foreground=palette["muted"])
        self.table.tag_configure("alt", background=palette["alt"])

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_box.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_controls(self) -> None:
        """Enable or disable controls for the current state."""
        running = self.running
        idle = "disabled" if running else "normal"
        self.start_button.configure(state="normal" if (not running and self.output_dir is not None) else "disabled")
        self.stop_button.configure(
            state="normal" if (running and not self.stop_event.is_set()) else "disabled",
            text="⏹ Stopping…" if (running and self.stop_event.is_set()) else "⏹ Stop",
        )
        self.folder_button.configure(state="normal" if self.output_dir is not None else "disabled")
        self.mode_switch.configure(state=idle)
        self.url_entry.configure(state=idle)
        self.browse_button.configure(state=idle)
        self.output_button.configure(state=idle)

    @staticmethod
    def _table_tags(index: int, tag: str) -> Tuple[str, ...]:
        return (tag, "alt") if index % 2 else (tag,)

    # ------------------------------------------------------------------ #
    # Input events
    # ------------------------------------------------------------------ #
    def _on_theme_change(self, choice: str) -> None:
        ctk.set_appearance_mode(choice)
        self._style_table()

    def _on_mode_change(self, mode: str) -> None:
        single = mode == MODE_SINGLE
        for widget in (self.url_label, self.url_entry, self.excel_label, self.excel_entry, self.browse_button):
            widget.grid_forget()
        if single:
            self.url_label.grid(row=1, column=0, sticky="w", padx=(18, 12), pady=8)
            self.url_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 18), pady=8)
        else:
            self.excel_label.grid(row=1, column=0, sticky="w", padx=(18, 12), pady=8)
            self.excel_entry.grid(row=1, column=1, sticky="ew", pady=8)
            self.browse_button.grid(row=1, column=2, padx=(10, 18), pady=8)

    def _on_browse_excel(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self, title="Choose the Excel list of URLs", filetypes=[("Excel workbook", "*.xlsx")]
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".xlsx":
            messagebox.showwarning("Unsupported file", "Please choose an Excel workbook that ends in .xlsx.",
                                   parent=self)
            return
        self.excel_path = path
        self.excel_var.set(str(path))

    def _on_choose_output(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="Choose the folder for downloaded PDFs", mustexist=True)
        if not chosen:
            return
        self.output_dir = Path(chosen)
        self.output_var.set(str(self.output_dir))
        self._refresh_controls()

    def _on_open_folder(self) -> None:
        if self.output_dir is None or not self.output_dir.is_dir():
            messagebox.showwarning("Folder not found", "The output folder no longer exists. Choose it again.",
                                   parent=self)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(self.output_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.output_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.output_dir)])
        except OSError as exc:
            messagebox.showerror("Could not open the folder", str(exc), parent=self)

    # ------------------------------------------------------------------ #
    # Start / Stop
    # ------------------------------------------------------------------ #
    def _collect_jobs(self) -> Optional[Tuple[List[Job], Optional[ExcelUrlList]]]:
        """Validate the inputs. Returns ``(jobs, excel)`` or ``None`` after telling the user why."""
        if self.mode_var.get() == MODE_SINGLE:
            raw = self.url_entry.get().strip()
            if not raw:
                messagebox.showwarning("URL needed", "Type the company's website address first.", parent=self)
                self.url_entry.focus_set()
                return None
            url = normalise_url(raw)
            if url is None:
                messagebox.showwarning(
                    "Check the URL",
                    f"\"{raw}\" does not look like a website address.\n\nExample: www.example.com",
                    parent=self,
                )
                self.url_entry.focus_set()
                return None
            return build_jobs([UrlRow(0, url)]), None

        if self.excel_path is None:
            messagebox.showwarning("Excel file needed", "Choose the Excel list with 📂 Browse Excel first.",
                                   parent=self)
            return None
        if not self.excel_path.is_file():
            messagebox.showerror("File not found", f"{self.excel_path}\n\nThe workbook has been moved or deleted.",
                                 parent=self)
            return None
        excel = ExcelUrlList(self.excel_path)
        try:
            rows = excel.read_urls()
        except ExcelFormatError as exc:
            messagebox.showerror("Cannot use this workbook", str(exc), parent=self)
            return None
        except PermissionError:
            messagebox.showerror(
                "Workbook is locked",
                f"{self.excel_path.name} cannot be read right now. Close it in Excel and press Start again.",
                parent=self,
            )
            return None
        except OSError as exc:
            messagebox.showerror("Cannot read the workbook", str(exc), parent=self)
            return None
        jobs = build_jobs(rows)
        duplicates = len(rows) - len(jobs)
        if duplicates:
            self._append_log(f"ℹ️ {duplicates} duplicate URL row(s) will reuse the result of their first occurrence")
        return jobs, excel

    def _on_start(self) -> None:
        if self.running:
            return
        if self.output_dir is None:
            messagebox.showwarning("Output folder needed", "Choose where to save the PDFs with 💾 Output Folder.",
                                   parent=self)
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            probe = self.output_dir / ".pdf_sourcing_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            messagebox.showerror("Cannot write to the output folder", f"{self.output_dir}\n\n{exc}", parent=self)
            return

        collected = self._collect_jobs()
        if collected is None:
            return
        jobs, excel = collected

        self._fill_table(jobs)
        self.progress.set(0)
        self.counter_var.set(f"0 of {len(jobs)}")
        self.phase_var.set("Starting…")
        self.stop_event = threading.Event()
        self.worker = SourcingWorker(jobs, self.output_dir, self.events, self.stop_event, excel)
        self.worker.start()
        self._refresh_controls()

    def _fill_table(self, jobs: List[Job]) -> None:
        self.table.delete(*self.table.get_children())
        self.row_ids.clear()
        for job in jobs:
            item = self.table.insert(
                "", "end", values=(job.url, STATUS_QUEUED, "", ""), tags=self._table_tags(job.index, "pending")
            )
            self.row_ids[job.index] = item

    def _on_stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.phase_var.set("Stopping after the current URL…")
        self._append_log("⏹ Stop requested: the run ends after the current URL")
        self._refresh_controls()

    # ------------------------------------------------------------------ #
    # Worker events
    # ------------------------------------------------------------------ #
    def _poll_events(self) -> None:
        """Drain the worker queue. Runs on the UI thread every POLL_INTERVAL_MS."""
        try:
            for _ in range(MAX_EVENTS_PER_POLL):
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        finally:
            if not self._closing:
                self.after(POLL_INTERVAL_MS, self._poll_events)

    def _handle_event(self, event: object) -> None:
        if isinstance(event, LogEvent):
            self._append_log(event.text)
        elif isinstance(event, RowEvent):
            item = self.row_ids.get(event.index)
            if item is not None:
                url = self.table.set(item, "url")
                self.table.item(
                    item, values=(url, event.status, event.ped, event.comments),
                    tags=self._table_tags(event.index, event.tag),
                )
                self.table.see(item)
        elif isinstance(event, ProgressEvent):
            self.progress.set(event.overall)
            self.counter_var.set(f"{event.done} of {event.total}")
            if not self.stop_event.is_set():
                self.phase_var.set(event.phase)
        elif isinstance(event, SaveBlockedEvent):
            event.retry = messagebox.askretrycancel(
                "Close the workbook to save",
                f"{event.path.name} is open in Excel, so the results cannot be saved yet.\n\n"
                "Close the workbook, then click Retry.\n\n"
                "Cancel keeps the original file unchanged and saves the results as a copy "
                "in the output folder instead.",
                parent=self,
            )
            event.answered.set()
        elif isinstance(event, FinishedEvent):
            self._on_finished(event)

    def _on_finished(self, event: FinishedEvent) -> None:
        self.worker = None
        self.progress.set(1.0 if not event.stopped else self.progress.get())
        self.phase_var.set("Stopped" if event.stopped else "Finished")
        self._refresh_controls()

        lines = [
            f"✅ Date found: {event.succeeded}",
            f"❌ {NOT_FOUND_TEXT}: {event.failed}",
        ]
        if event.cancelled:
            lines.append(f"⏹ Cancelled: {event.cancelled}")
        if event.excel_saved_to is not None:
            lines.append(f"\n💾 Workbook saved:\n{event.excel_saved_to}")
        if event.log_path is not None:
            lines.append(f"\n📝 Log with the reason for each failure:\n{event.log_path}")
        summary = "\n".join(lines)

        if event.excel_error:
            messagebox.showerror("Finished, but the workbook was not saved",
                                 f"{event.excel_error}\n\n{summary}", parent=self)
        elif event.stopped:
            messagebox.showinfo("Run stopped", summary, parent=self)
        else:
            messagebox.showinfo("Run finished", summary, parent=self)

    # ------------------------------------------------------------------ #
    def _on_close(self) -> None:
        if self.running:
            leave = messagebox.askyesno(
                "A run is in progress",
                "Closing now stops the run and results that are not saved yet are lost.\n\nClose anyway?",
                parent=self,
            )
            if not leave:
                return
            self.stop_event.set()
        self._closing = True
        self.destroy()


def run_app() -> None:
    """Create the window and start the Tk main loop."""
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
