from __future__ import annotations

import json
import queue
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from .analyze import analyze_song, format_analysis_text
from .ast import to_jsonable
from .core import song_stats
from .io_utils import write_bytes_atomic, write_text_atomic
from .lint import lint_song
from .lyrics import build_lyrics_alignment_report
from .midi_dump import dump_midi_text
from .musicxml import prepare_musicxml_export
from .parser import format_song, parse_song
from .render import render_midi_bytes
from .styles import expand_song_templates

# Centralized theme tokens keep the AMOLED palette easy to change later.
GUI_THEME = {
    "bg": "#0B1120",
    "bg_alt": "#111827",
    "panel": "#121A2B",
    "panel_alt": "#182235",
    "panel_elevated": "#1D2940",
    "border": "#2B3854",
    "border_strong": "#47597D",
    "shadow": "#060A13",
    "text": "#F8FAFC",
    "muted_text": "#C7D2E4",
    "placeholder": "#6B7A94",
    "hover": "#263755",
    "hover_text": "#FFFFFF",
    "active": "#7C9BFF",
    "active_text": "#08111F",
    "accent": "#7C9BFF",
    "accent_soft": "#B8C7FF",
    "accent_alt": "#5EEAD4",
    "danger": "#FF5F5F",
    "success": "#9FE870",
    "font": "TkDefaultFont",
    "mono": "TkFixedFont",
    "radius": 16,
    "border_width": 1,
    "window_width": 1380,
    "window_height": 900,
}


class SongScriptService:
    """Thin application layer that isolates the GUI from compiler internals."""

    def lint(self, text: str, strict: bool) -> dict[str, str]:
        issues = lint_song(text, strict=strict)
        lines = [issue.format_line() for issue in issues]
        errors = sum(1 for issue in issues if issue.level == "ERROR")
        warnings = sum(1 for issue in issues if issue.level == "WARN")
        summary = f"errors={errors} warnings={warnings}"
        return {"title": "Lint", "body": "\n".join(lines) if lines else "No lint issues.", "summary": summary}

    def analyze(self, text: str) -> dict[str, str]:
        analysis = analyze_song(text)
        summary = {
            "sections": analysis["global"]["sections"],
            "rendered_bars": analysis["playback"]["total_rendered_bars"],
            "melody_events": analysis["melody"]["total_events"],
            "warnings": len(analysis["warnings"]),
        }
        return {
            "title": "Analysis",
            "body": format_analysis_text(analysis),
            "summary": json.dumps(summary, sort_keys=True),
        }

    def format_source(self, text: str) -> dict[str, str]:
        formatted = format_song(text)
        return {"title": "Formatter", "body": formatted, "summary": "canonicalized"}

    def stats(self, text: str) -> dict[str, str]:
        payload = song_stats(text)
        return {"title": "Stats", "body": json.dumps(payload, indent=2, sort_keys=True), "summary": "stats ready"}

    def lyrics_report(self, text: str) -> dict[str, str]:
        song = expand_song_templates(parse_song(text))
        report = build_lyrics_alignment_report(song)
        lines: list[str] = []
        for section in report:
            lines.append(
                f"{section.section_name}#{section.section_instance}: "
                f"melody_notes={section.melody_event_count} "
                f"lyric_tokens={section.lyric_token_count} "
                f"overflow={section.overflow_count} "
                f"orphan_extenders={section.orphan_extenders} "
                f"estimated_syllables={section.estimated_syllables}"
            )
            for bar in section.bars:
                lines.append(
                    f"  bar {bar.bar_index}: "
                    f"melody_notes={bar.melody_event_count} "
                    f"lyric_tokens={bar.lyric_token_count} "
                    f"overflow={bar.overflow_count} "
                    f"orphan_extenders={bar.orphan_extenders} "
                    f"estimated_syllables={bar.estimated_syllables}"
                )
        return {"title": "Lyrics", "body": "\n".join(lines) if lines else "No lyric data.", "summary": f"sections={len(report)}"}

    def export_midi(self, text: str, output_path: str, strict: bool) -> dict[str, str]:
        midi_bytes = render_midi_bytes(text, strict=strict)
        write_bytes_atomic(Path(output_path), midi_bytes)
        return {"title": "MIDI Export", "body": f"Wrote {len(midi_bytes)} bytes to\n{output_path}", "summary": "midi written"}

    def export_musicxml(self, text: str, output_path: str) -> dict[str, str]:
        warnings, xml_text = prepare_musicxml_export(text)
        write_text_atomic(Path(output_path), xml_text)
        body = f"Wrote MusicXML to\n{output_path}"
        if warnings:
            body += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)
        return {"title": "MusicXML Export", "body": body, "summary": f"warnings={len(warnings)}"}

    def export_ast(self, text: str, output_path: str) -> dict[str, str]:
        payload = to_jsonable(parse_song(text))
        write_text_atomic(Path(output_path), json.dumps(payload, indent=2))
        return {"title": "AST Export", "body": f"Wrote AST JSON to\n{output_path}", "summary": "ast written"}

    def dump_midi(self, text: str, output_path: str) -> dict[str, str]:
        dumped = dump_midi_text(render_midi_bytes(text))
        write_text_atomic(Path(output_path), dumped)
        return {"title": "MIDI Dump", "body": f"Wrote event dump to\n{output_path}", "summary": "dump written"}


class ThemedCard(tk.Frame):
    def __init__(self, master, title: str, **kwargs):
        super().__init__(master, bg=GUI_THEME["bg"], bd=0, highlightthickness=0, **kwargs)
        self.configure(padx=0, pady=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.shadow = tk.Frame(self, bg=GUI_THEME["shadow"], bd=0, highlightthickness=0)
        self.shadow.place(relx=0, rely=0, relwidth=1, relheight=1, x=4, y=6)
        self.surface = tk.Frame(
            self,
            bg=GUI_THEME["panel"],
            highlightbackground=GUI_THEME["border"],
            highlightthickness=1,
            bd=0,
        )
        self.surface.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.title_label = tk.Label(
            self.surface,
            text=title,
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["text"],
            font=(GUI_THEME["font"], 11, "bold"),
            anchor="w",
            padx=18,
            pady=14,
        )
        self.title_label.grid(row=0, column=0, sticky="ew")
        self.accent_line = tk.Frame(self.surface, bg=GUI_THEME["accent"], height=2)
        self.accent_line.grid(row=1, column=0, sticky="ew", padx=18)
        self.content = tk.Frame(self.surface, bg=GUI_THEME["panel"], bd=0, highlightthickness=0)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.surface.grid_columnconfigure(0, weight=1)
        self.surface.grid_rowconfigure(2, weight=1)

    def __getattr__(self, item):
        if item in {"tk", "_w", "children", "master"}:
            raise AttributeError(item)
        return getattr(self.content, item)


class PlaceholderEntry(tk.Frame):
    def __init__(self, master, placeholder: str, textvariable=None, **kwargs):
        self.placeholder = placeholder
        self.placeholder_color = GUI_THEME["placeholder"]
        self.default_fg = GUI_THEME["text"]
        self._showing_placeholder = False
        super().__init__(master, bg=GUI_THEME["bg"], bd=0, highlightthickness=0)
        self.shadow = tk.Frame(self, bg=GUI_THEME["shadow"], bd=0, highlightthickness=0, height=1)
        self.shadow.place(relx=0, rely=0, relwidth=1, relheight=1, y=3)
        self.container = tk.Frame(
            self,
            bg=GUI_THEME["panel_elevated"],
            highlightbackground=GUI_THEME["border"],
            highlightthickness=1,
            bd=0,
        )
        self.container.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.entry = tk.Entry(
            self.container,
            textvariable=textvariable,
            bg=GUI_THEME["panel_elevated"],
            fg=self.default_fg,
            insertbackground=GUI_THEME["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            **kwargs,
        )
        self.entry.pack(fill="both", expand=True, padx=14, pady=10)
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._apply_placeholder)
        self._apply_placeholder()

    def _apply_placeholder(self, _event=None):
        if not self.entry.get():
            self._showing_placeholder = True
            self.entry.delete(0, "end")
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=self.placeholder_color)

    def _clear_placeholder(self, _event=None):
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=self.default_fg)

    def value(self) -> str:
        return "" if self._showing_placeholder else self.entry.get().strip()

    def set_value(self, value: str) -> None:
        self._showing_placeholder = False
        self.entry.configure(fg=self.default_fg)
        self.entry.delete(0, "end")
        self.entry.insert(0, value)


class RoundedButton(tk.Canvas):
    def __init__(self, master, text: str, command, width: int = 180, height: int = 38, accent: bool = False):
        super().__init__(
            master,
            width=width,
            height=height,
            bg=master.cget("bg"),
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self.accent = accent
        self.is_hover = False
        self.is_pressed = False
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=36, **kwargs)

    def _colors(self):
        if self.is_pressed:
            return GUI_THEME["accent_soft"], GUI_THEME["active_text"], GUI_THEME["accent"]
        if self.is_hover:
            return GUI_THEME["hover"], GUI_THEME["hover_text"], GUI_THEME["accent"]
        if self.accent:
            return GUI_THEME["active"], GUI_THEME["active_text"], GUI_THEME["accent_soft"]
        return GUI_THEME["panel_elevated"], GUI_THEME["text"], GUI_THEME["border_strong"]

    def _draw(self):
        self.delete("all")
        fill, text_color, outline = self._colors()
        self._rounded_rect(
            4,
            6,
            self.width - 4,
            self.height - 2,
            GUI_THEME["radius"],
            fill=GUI_THEME["shadow"],
            outline=GUI_THEME["shadow"],
            width=1,
        )
        self._rounded_rect(
            2,
            2,
            self.width - 2,
            self.height - 6,
            GUI_THEME["radius"],
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_line(16, 8, self.width - 16, 8, fill=GUI_THEME["accent_soft"] if self.accent or self.is_hover else GUI_THEME["border"], width=1)
        self.create_text(
            self.width / 2,
            (self.height / 2) - 2,
            text=self.text,
            fill=text_color,
            font=(GUI_THEME["font"], 10, "bold"),
        )

    def _on_enter(self, _event):
        self.is_hover = True
        self._draw()

    def _on_leave(self, _event):
        self.is_hover = False
        self.is_pressed = False
        self._draw()

    def _on_press(self, _event):
        self.is_pressed = True
        self._draw()

    def _on_release(self, event):
        self.is_pressed = False
        self._draw()
        if 0 <= event.x <= self.width and 0 <= event.y <= self.height:
            self.command()


class MonoToggle(tk.Canvas):
    def __init__(self, master, variable: tk.BooleanVar, command=None):
        super().__init__(master, width=58, height=30, bg=GUI_THEME["panel"], highlightthickness=0)
        self.variable = variable
        self.command = command
        self.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        enabled = self.variable.get()
        fill = GUI_THEME["active"] if enabled else GUI_THEME["panel_elevated"]
        knob = GUI_THEME["bg"] if enabled else GUI_THEME["muted_text"]
        outline = GUI_THEME["accent_soft"] if enabled else GUI_THEME["border"]
        self.create_oval(4, 6, 54, 28, fill=GUI_THEME["shadow"], outline=GUI_THEME["shadow"])
        self.create_oval(2, 2, 56, 26, fill=fill, outline=outline, width=1)
        knob_x = 30 if enabled else 6
        self.create_oval(knob_x, 5, knob_x + 20, 25, fill=knob, outline=outline, width=1)

    def _toggle(self, _event):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()


class NavButton(tk.Frame):
    def __init__(self, master, icon: str, label: str, command):
        super().__init__(master, bg=GUI_THEME["bg"])
        self.command = command
        self.active = False
        self.inner = tk.Frame(self, bg=GUI_THEME["panel"], highlightthickness=1, highlightbackground=GUI_THEME["border"], bd=0)
        self.inner.pack(fill="x", padx=8, pady=6)
        self.glow = tk.Frame(self.inner, bg=GUI_THEME["shadow"], height=3)
        self.glow.pack(fill="x")
        self.icon_label = tk.Label(self.inner, text=icon, bg=GUI_THEME["panel"], fg=GUI_THEME["accent_soft"], font=(GUI_THEME["font"], 15))
        self.icon_label.pack(pady=(8, 0))
        self.text_label = tk.Label(self.inner, text=label, bg=GUI_THEME["panel"], fg=GUI_THEME["muted_text"], font=(GUI_THEME["font"], 9))
        self.text_label.pack(pady=(2, 10))
        for widget in (self, self.inner, self.icon_label, self.text_label):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._hover_in)
            widget.bind("<Leave>", self._hover_out)

    def set_active(self, active: bool):
        self.active = active
        bg = GUI_THEME["active"] if active else GUI_THEME["panel"]
        fg = GUI_THEME["active_text"] if active else GUI_THEME["accent_soft"]
        muted = GUI_THEME["active_text"] if active else GUI_THEME["muted_text"]
        border = GUI_THEME["accent_soft"] if active else GUI_THEME["border"]
        self.inner.configure(bg=bg, highlightbackground=border)
        self.glow.configure(bg=GUI_THEME["shadow"] if not active else GUI_THEME["accent_soft"])
        self.icon_label.configure(bg=bg, fg=fg)
        self.text_label.configure(bg=bg, fg=muted)

    def _hover_in(self, _event):
        if not self.active:
            self.inner.configure(bg=GUI_THEME["hover"], highlightbackground=GUI_THEME["border_strong"])
            self.icon_label.configure(bg=GUI_THEME["hover"])
            self.text_label.configure(bg=GUI_THEME["hover"])

    def _hover_out(self, _event):
        if not self.active:
            self.set_active(False)

    def _on_click(self, _event):
        self.command()


class SongScriptGUI(tk.Tk):
    """AMOLED-first desktop shell for the SongScript compiler.

    Layout management:
    - Root uses a 1px white outer frame to simulate a custom bordered window.
    - The internal shell is a three-row grid: title bar, main body, console/status band.
    - The main body is a two-column grid with a fixed-width sidebar and a fully weighted content area.
    - Every page uses nested weighted grids so cards stretch without overlapping as the window resizes.
    """

    def __init__(self):
        super().__init__()
        self.title("SongScript Studio")
        self.configure(bg=GUI_THEME["shadow"])
        self.overrideredirect(True)
        self.minsize(1120, 760)
        self._center_window(GUI_THEME["window_width"], GUI_THEME["window_height"])

        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="songscr-gui")
        self.pending_results: queue.Queue[tuple[str, Future]] = queue.Queue()
        self.service = SongScriptService()
        self.current_file = Path.cwd() / "sample.songscr"
        self.strict_mode = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Idle")
        self.summary_var = tk.StringVar(value="No task executed yet.")
        self.result_title_var = tk.StringVar(value="Console")

        self._configure_ttk()
        self._build_window_shell()
        self._build_titlebar()
        self._build_sidebar()
        self._build_pages()
        self._build_console()
        self._bind_window_controls()
        self._load_initial_document()
        self.after(80, self._drain_task_queue)

    def _configure_ttk(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Mono.Vertical.TScrollbar",
            background=GUI_THEME["panel_elevated"],
            troughcolor=GUI_THEME["panel"],
            bordercolor=GUI_THEME["border"],
            darkcolor=GUI_THEME["panel_elevated"],
            lightcolor=GUI_THEME["panel_elevated"],
            arrowcolor=GUI_THEME["accent_soft"],
            relief="flat",
        )

    def _center_window(self, width: int, height: int):
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, int((screen_w - width) / 2))
        y = max(0, int((screen_h - height) / 2))
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_window_shell(self):
        self.shell = tk.Frame(self, bg=GUI_THEME["bg"])
        self.shell.pack(fill="both", expand=True, padx=8, pady=8)
        self.shell.grid_rowconfigure(1, weight=1)
        self.shell.grid_columnconfigure(0, weight=1)

    def _build_titlebar(self):
        self.titlebar = tk.Frame(self.shell, bg=GUI_THEME["panel"], height=54, highlightbackground=GUI_THEME["border"], highlightthickness=1)
        self.titlebar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.titlebar.grid_columnconfigure(1, weight=1)
        self.titlebar.bind("<ButtonPress-1>", self._start_move)
        self.titlebar.bind("<B1-Motion>", self._on_move)

        brand = tk.Label(
            self.titlebar,
            text="SONGSCR STUDIO",
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["text"],
            font=(GUI_THEME["font"], 12, "bold"),
            padx=18,
            pady=8,
        )
        brand.grid(row=0, column=0, sticky="w")
        brand.bind("<ButtonPress-1>", self._start_move)
        brand.bind("<B1-Motion>", self._on_move)

        self.window_state = tk.Label(
            self.titlebar,
            textvariable=self.status_var,
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["muted_text"],
            font=(GUI_THEME["font"], 10),
        )
        self.window_state.grid(row=0, column=1, sticky="w")

        button_bar = tk.Frame(self.titlebar, bg=GUI_THEME["panel"])
        button_bar.grid(row=0, column=2, sticky="e", padx=10)
        for label, command in (("—", self.iconify), ("✕", self.destroy)):
            btn = tk.Label(button_bar, text=label, bg=GUI_THEME["panel_elevated"], fg=GUI_THEME["text"], width=4, font=(GUI_THEME["font"], 11, "bold"))
            btn.pack(side="left", padx=4, pady=10)
            btn.bind("<Button-1>", lambda _event, fn=command: fn())
            btn.bind("<Enter>", lambda event: event.widget.configure(bg=GUI_THEME["hover"], fg=GUI_THEME["hover_text"]))
            btn.bind("<Leave>", lambda event: event.widget.configure(bg=GUI_THEME["panel_elevated"], fg=GUI_THEME["text"]))

    def _build_sidebar(self):
        self.body = tk.Frame(self.shell, bg=GUI_THEME["bg"])
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(
            self.body,
            bg=GUI_THEME["panel"],
            width=112,
            highlightbackground=GUI_THEME["border"],
            highlightthickness=1,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        self.sidebar.grid_propagate(False)

        sidebar_header = tk.Label(
            self.sidebar,
            text="NAV",
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["placeholder"],
            font=(GUI_THEME["font"], 9, "bold"),
            pady=14,
        )
        sidebar_header.pack(fill="x")

        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, NavButton] = {}
        nav_spec = [
            ("workspace", "◫", "Work"),
            ("analysis", "⌁", "Review"),
            ("exports", "⬒", "Export"),
            ("about", "◎", "About"),
        ]
        for page_id, icon, label in nav_spec:
            button = NavButton(self.sidebar, icon, label, command=lambda page_id=page_id: self.show_page(page_id))
            button.pack(fill="x", pady=4)
            self.nav_buttons[page_id] = button

    def _build_pages(self):
        self.page_host = tk.Frame(self.body, bg=GUI_THEME["bg"])
        self.page_host.grid(row=0, column=1, sticky="nsew")
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)

        self._build_workspace_page()
        self._build_analysis_page()
        self._build_exports_page()
        self._build_about_page()
        self.show_page("workspace")

    def _build_workspace_page(self):
        page = tk.Frame(self.page_host, bg=GUI_THEME["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=3)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages["workspace"] = page

        editor_card = ThemedCard(page, "Source Workspace")
        editor_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        editor_card.content.grid_columnconfigure(0, weight=1)
        editor_card.content.grid_rowconfigure(1, weight=1)

        path_row = tk.Frame(editor_card.content, bg=GUI_THEME["panel"])
        path_row.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
        path_row.grid_columnconfigure(0, weight=1)
        self.file_entry = PlaceholderEntry(path_row, "Open a .songscr file or work from the editor buffer")
        self.file_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), ipady=2)
        RoundedButton(path_row, "Open", self.open_file, width=96).grid(row=0, column=1, sticky="e")

        editor_frame = tk.Frame(editor_card.content, bg=GUI_THEME["panel"])
        editor_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        editor_frame.grid_rowconfigure(0, weight=1)
        editor_frame.grid_columnconfigure(0, weight=1)
        self.editor = tk.Text(
            editor_frame,
            wrap="none",
            undo=True,
            bg=GUI_THEME["bg_alt"],
            fg=GUI_THEME["text"],
            insertbackground=GUI_THEME["text"],
            selectbackground=GUI_THEME["accent"],
            selectforeground=GUI_THEME["active_text"],
            relief="flat",
            highlightbackground=GUI_THEME["border"],
            highlightthickness=1,
            font=(GUI_THEME["mono"], 11),
            padx=18,
            pady=18,
        )
        self.editor.grid(row=0, column=0, sticky="nsew")
        editor_scroll = ttk.Scrollbar(editor_frame, orient="vertical", style="Mono.Vertical.TScrollbar", command=self.editor.yview)
        editor_scroll.grid(row=0, column=1, sticky="ns")
        self.editor.configure(yscrollcommand=editor_scroll.set)

        action_card = ThemedCard(page, "Actions")
        action_card.grid(row=0, column=1, sticky="nsew")
        action_card.content.grid_columnconfigure(0, weight=1)

        hero = tk.Label(
            action_card.content,
            text="Compile, inspect, and export from a single workspace with non-blocking background tasks.",
            justify="left",
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["muted_text"],
            font=(GUI_THEME["font"], 10),
            padx=18,
            pady=18,
            wraplength=280,
        )
        hero.grid(row=0, column=0, sticky="ew")

        strict_row = tk.Frame(action_card.content, bg=GUI_THEME["panel"])
        strict_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        tk.Label(strict_row, text="Strict lint", bg=GUI_THEME["panel"], fg=GUI_THEME["muted_text"]).pack(side="left")
        MonoToggle(strict_row, self.strict_mode).pack(side="right")

        button_grid = tk.Frame(action_card.content, bg=GUI_THEME["panel"])
        button_grid.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 14))
        for idx in range(2):
            button_grid.grid_columnconfigure(idx, weight=1)
        buttons = [
            ("Save", self.save_file, True),
            ("Format", self.format_buffer, False),
            ("Lint", self.run_lint, False),
            ("Analyze", self.run_analysis, False),
            ("Stats", self.run_stats, False),
            ("Lyrics", self.run_lyrics_report, False),
        ]
        for index, (label, command, accent) in enumerate(buttons):
            RoundedButton(button_grid, label, command, width=140, accent=accent).grid(
                row=index // 2,
                column=index % 2,
                padx=6,
                pady=6,
                sticky="ew",
            )

        notes = tk.Label(
            action_card.content,
            text="Soft elevation and restrained contrast keep the interface readable without feeling flat.\nEvery action remains threaded to avoid UI stalls during heavy compiler work.",
            justify="left",
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["muted_text"],
            font=(GUI_THEME["font"], 9),
            padx=18,
            pady=8,
            wraplength=280,
        )
        notes.grid(row=3, column=0, sticky="ew")

    def _build_analysis_page(self):
        page = tk.Frame(self.page_host, bg=GUI_THEME["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.pages["analysis"] = page

        summary_card = ThemedCard(page, "Execution Summary")
        summary_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14), pady=(0, 14))
        self.summary_label = tk.Label(
            summary_card.content,
            textvariable=self.summary_var,
            justify="left",
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["muted_text"],
            font=(GUI_THEME["mono"], 10),
            padx=18,
            pady=18,
            anchor="w",
        )
        self.summary_label.grid(row=0, column=0, sticky="nsew")

        stats_card = ThemedCard(page, "Structured Metrics")
        stats_card.grid(row=0, column=1, sticky="nsew", pady=(0, 14))
        self.stats_view = self._build_readonly_text(stats_card.content, row=0, padx=18, pady=(18, 18))

        report_card = ThemedCard(page, "Detailed Review")
        report_card.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.analysis_view = self._build_readonly_text(report_card.content, row=0, padx=18, pady=(18, 18))

    def _build_exports_page(self):
        page = tk.Frame(self.page_host, bg=GUI_THEME["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_columnconfigure(1, weight=1)
        self.pages["exports"] = page

        export_specs = [
            ("MIDI Render", "sample.mid", self.export_midi),
            ("MusicXML", "sample.musicxml", self.export_musicxml),
            ("AST JSON", "sample.ast.json", self.export_ast),
            ("Event Dump", "sample.dump.txt", self.export_dump),
        ]
        self.export_entries: dict[str, PlaceholderEntry] = {}
        for index, (title, default_name, command) in enumerate(export_specs):
            card = ThemedCard(page, title)
            row = index // 2
            col = index % 2
            card.grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 14, 0), pady=(0 if row == 0 else 14, 0))
            card.content.grid_columnconfigure(0, weight=1)
            default_path = str((Path.cwd() / default_name).resolve())
            entry = PlaceholderEntry(card.content, default_path)
            entry.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10), ipady=2)
            entry.set_value(default_path)
            self.export_entries[title] = entry

            actions = tk.Frame(card.content, bg=GUI_THEME["panel"])
            actions.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 18))
            actions.grid_columnconfigure(0, weight=1)
            RoundedButton(actions, "Browse", lambda entry=entry: self.choose_output_path(entry), width=110).grid(row=0, column=0, sticky="w")
            RoundedButton(actions, "Run", command, width=110, accent=True).grid(row=0, column=1, sticky="e")

    def _build_about_page(self):
        page = tk.Frame(self.page_host, bg=GUI_THEME["bg"])
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages["about"] = page

        card = ThemedCard(page, "Interface Notes")
        card.grid(row=0, column=0, sticky="nsew")
        body = tk.Label(
            card.content,
            justify="left",
            anchor="nw",
            bg=GUI_THEME["panel"],
            fg=GUI_THEME["muted_text"],
            font=(GUI_THEME["font"], 10),
            padx=18,
            pady=18,
            text=(
                "Design decisions\n"
                "- Layered surfaces and restrained shadows create subtle depth.\n"
                "- Rounded geometry keeps the interface soft and contemporary.\n"
                "- Accent color is reserved for focus and action hierarchy.\n"
                "- Worker threads isolate lint, analysis, render, and export tasks from the Tk event loop.\n\n"
                "Navigation\n"
                "- Work: source editing and primary actions.\n"
                "- Review: stats and detailed compiler output.\n"
                "- Export: output path management for generated artifacts.\n"
            ),
        )
        body.grid(row=1, column=0, sticky="nsew")

    def _build_console(self):
        console_card = ThemedCard(self.shell, "Output Console")
        console_card.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        console_card.content.grid_rowconfigure(1, weight=1)
        console_card.content.grid_columnconfigure(0, weight=1)

        header = tk.Frame(console_card.content, bg=GUI_THEME["panel"])
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(1, weight=1)
        tk.Label(header, textvariable=self.result_title_var, bg=GUI_THEME["panel"], fg=GUI_THEME["text"], font=(GUI_THEME["font"], 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, textvariable=self.summary_var, bg=GUI_THEME["panel"], fg=GUI_THEME["muted_text"], font=(GUI_THEME["mono"], 9)).grid(row=0, column=1, sticky="e")

        self.console = self._build_readonly_text(console_card.content, row=1, padx=18, pady=(0, 18))

    def _build_readonly_text(self, parent, row: int, padx: int, pady):
        frame = tk.Frame(parent, bg=GUI_THEME["panel"])
        frame.grid(row=row, column=0, sticky="nsew", padx=padx, pady=pady)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        shadow = tk.Frame(frame, bg=GUI_THEME["shadow"])
        shadow.place(relx=0, rely=0, relwidth=1, relheight=1, x=3, y=4)
        surface = tk.Frame(frame, bg=GUI_THEME["panel_elevated"], highlightbackground=GUI_THEME["border"], highlightthickness=1)
        surface.place(relx=0, rely=0, relwidth=1, relheight=1)
        widget = tk.Text(
            surface,
            wrap="word",
            bg=GUI_THEME["bg_alt"],
            fg=GUI_THEME["text"],
            insertbackground=GUI_THEME["text"],
            relief="flat",
            highlightthickness=0,
            font=(GUI_THEME["mono"], 10),
            padx=16,
            pady=16,
        )
        surface.grid_rowconfigure(0, weight=1)
        surface.grid_columnconfigure(0, weight=1)
        widget.grid(row=0, column=0, sticky="nsew")
        widget.configure(state="disabled")
        scrollbar = ttk.Scrollbar(surface, orient="vertical", style="Mono.Vertical.TScrollbar", command=widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        widget.configure(yscrollcommand=scrollbar.set)
        return widget

    def _bind_window_controls(self):
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-s>", lambda _event: self.save_file())

    def _load_initial_document(self):
        if self.current_file.exists():
            self.file_entry.set_value(str(self.current_file.resolve()))
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.current_file.read_text(encoding="utf-8"))

    def _start_move(self, event):
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _on_move(self, event):
        offset_x, offset_y = getattr(self, "_drag_offset", (0, 0))
        self.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def show_page(self, page_id: str):
        for name, page in self.pages.items():
            page.grid_remove()
            self.nav_buttons[name].set_active(False)
        self.pages[page_id].grid()
        self.nav_buttons[page_id].set_active(True)

    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[("SongScript", "*.songscr"), ("Text", "*.txt"), ("All Files", "*.*")])
        if not path:
            return
        self.current_file = Path(path)
        self.file_entry.set_value(path)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.current_file.read_text(encoding="utf-8"))
        self._set_console("File", f"Loaded\n{path}", "document loaded")

    def choose_output_path(self, entry: PlaceholderEntry):
        path = filedialog.asksaveasfilename()
        if path:
            entry.set_value(path)

    def save_file(self):
        target = self.file_entry.value()
        if not target:
            target = filedialog.asksaveasfilename(defaultextension=".songscr", filetypes=[("SongScript", "*.songscr")])
        if not target:
            return
        self.current_file = Path(target)
        self.file_entry.set_value(target)
        write_text_atomic(self.current_file, self._editor_text())
        self._set_console("Save", f"Saved source to\n{target}", "source written")

    def format_buffer(self):
        self._submit_task("Formatter", lambda: self.service.format_source(self._editor_text()), apply_to_editor=True)

    def run_lint(self):
        strict = self.strict_mode.get()
        self._submit_task("Lint", lambda: self.service.lint(self._editor_text(), strict))

    def run_analysis(self):
        self._submit_task("Analysis", lambda: self.service.analyze(self._editor_text()), update_analysis=True)

    def run_stats(self):
        self._submit_task("Stats", lambda: self.service.stats(self._editor_text()), update_stats=True)

    def run_lyrics_report(self):
        self._submit_task("Lyrics", lambda: self.service.lyrics_report(self._editor_text()), update_analysis=True)

    def export_midi(self):
        strict = self.strict_mode.get()
        output = self.export_entries["MIDI Render"].value()
        self._submit_task("MIDI Export", lambda: self.service.export_midi(self._editor_text(), output, strict))

    def export_musicxml(self):
        output = self.export_entries["MusicXML"].value()
        self._submit_task("MusicXML Export", lambda: self.service.export_musicxml(self._editor_text(), output))

    def export_ast(self):
        output = self.export_entries["AST JSON"].value()
        self._submit_task("AST Export", lambda: self.service.export_ast(self._editor_text(), output))

    def export_dump(self):
        output = self.export_entries["Event Dump"].value()
        self._submit_task("MIDI Dump", lambda: self.service.dump_midi(self._editor_text(), output))

    def _submit_task(self, label: str, fn, apply_to_editor: bool = False, update_analysis: bool = False, update_stats: bool = False):
        self.status_var.set(f"{label} running…")
        future = self.executor.submit(fn)
        future._songscr_apply_to_editor = apply_to_editor  # type: ignore[attr-defined]
        future._songscr_update_analysis = update_analysis  # type: ignore[attr-defined]
        future._songscr_update_stats = update_stats  # type: ignore[attr-defined]
        self.pending_results.put((label, future))

    def _drain_task_queue(self):
        pending: list[tuple[str, Future]] = []
        while not self.pending_results.empty():
            label, future = self.pending_results.get()
            if not future.done():
                pending.append((label, future))
                continue
            try:
                result = future.result()
            except Exception as exc:  # surfaced to the user, not swallowed silently
                self._set_console(label, str(exc), "task failed")
                self.status_var.set(f"{label} failed")
                continue

            body = result["body"]
            summary = result.get("summary", "")
            self._set_console(result["title"], body, summary)
            if getattr(future, "_songscr_apply_to_editor", False):
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", body)
            if getattr(future, "_songscr_update_analysis", False):
                self._write_readonly(self.analysis_view, body)
            if getattr(future, "_songscr_update_stats", False):
                self._write_readonly(self.stats_view, body)
            self.status_var.set(f"{label} complete")

        for item in pending:
            self.pending_results.put(item)
        self.after(80, self._drain_task_queue)

    def _set_console(self, title: str, body: str, summary: str):
        self.result_title_var.set(title)
        self.summary_var.set(summary)
        self._write_readonly(self.console, body)

    def _write_readonly(self, widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _editor_text(self) -> str:
        return self.editor.get("1.0", "end-1c")

    def destroy(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().destroy()


def launch() -> None:
    app = SongScriptGUI()
    app.mainloop()


if __name__ == "__main__":
    launch()
