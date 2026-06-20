"""
ChronoFlex - A modern Windows timer application.

Features
--------
* Precise mode: set duration in days / hours / minutes / seconds.
* Random mode: pick a random duration inside a user-defined range.
* Timer presets: quick-start buttons for common durations.
* Circular progress ring with adaptive color (blue -> amber -> red).
* Start / Pause / Resume / Reset.
* Flashing visual + repeated beep alarm on completion.
* Windows toast notification on completion.
* Timer history log (saved to JSON).
* Dark / Light theme toggle.
* Keyboard shortcuts: Space, R, Escape.
* Settings persistence across sessions.
"""

import json
import logging
import os
import random
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Literal

import winsound

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DATA_DIR = Path(os.environ.get("CHRONOFLEX_DATA", Path.home() / ".chronoflex"))
_SETTINGS_FILE = _DATA_DIR / "settings.json"
_HISTORY_FILE = _DATA_DIR / "history.json"


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_settings() -> dict:
    _ensure_data_dir()
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_settings(settings: dict) -> None:
    _ensure_data_dir()
    _SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _load_history() -> list[dict]:
    _ensure_data_dir()
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_history(history: list[dict]) -> None:
    _ensure_data_dir()
    _HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


def _send_toast(title: str, message: str) -> None:
    """Send a Windows toast notification if windows-toasts is available."""
    try:
        from windows_toasts import Toast, WindowsToaster

        toaster = WindowsToaster("ChronoFlex")
        toast = Toast()
        toast.text_fields = [title, message]
        toaster.show_toast(toast)
    except ImportError:
        logger.debug("windows-toasts not installed; toast notification skipped")
    except Exception:
        logger.exception("Failed to send toast notification")


# ---------------------------------------------------------------------------
# ToolTip helper
# ---------------------------------------------------------------------------
class ToolTip:
    """A simple tooltip that appears on hover over a tkinter widget."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip_window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, event: tk.Event) -> None:
        self._after_id = self.widget.after(self.delay, self._show_tip)

    def _on_leave(self, event: tk.Event) -> None:
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide_tip()

    def _show_tip(self) -> None:
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", foreground="#000000",
            relief="solid", borderwidth=1,
            font=("Segoe UI", 9),
        )
        label.pack()

    def _hide_tip(self) -> None:
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class InvalidRangeError(ValueError):
    """Raised when the random range inputs are invalid."""


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------
THEMES = {
    "dark": {
        "BG": "#1e1e2e",
        "CARD": "#313244",
        "CARD_ALT": "#2a2a3c",
        "INPUT_BG": "#45475a",
        "ACCENT": "#89b4fa",
        "ACCENT_HOV": "#b4d0fb",
        "TEXT": "#cdd6f4",
        "MUTED": "#a6adc8",
        "SUBTLE": "#6c7086",
        "DANGER": "#f38ba8",
        "SUCCESS": "#a6e3a1",
        "WARNING": "#f9e2af",
    },
    "light": {
        "BG": "#eff1f5",
        "CARD": "#ffffff",
        "CARD_ALT": "#e6e9ef",
        "INPUT_BG": "#ccd0da",
        "ACCENT": "#1e66f5",
        "ACCENT_HOV": "#4d8af7",
        "TEXT": "#4c4f69",
        "MUTED": "#6c6f85",
        "SUBTLE": "#9ca0b0",
        "DANGER": "#d20f39",
        "SUCCESS": "#40a02b",
        "WARNING": "#df8e1d",
    },
}


class ChronoFlex:
    # ---- Timing / alarm constants ----
    _TICK_INTERVAL: float = 0.1
    _ROUNDING_OFFSET: float = 0.999
    _TOPMOST_DURATION_MS: int = 2500
    _FLASH_INTERVAL_MS: int = 400
    _BEEP_FREQUENCY: int = 880
    _BEEP_DURATION_MS: int = 200
    _BEEP_PAUSE: float = 0.05
    _BEEP_GROUP_PAUSE: float = 0.4
    _BEEPS_PER_GROUP: int = 3

    # ---- Presets (label, seconds) ----
    PRESETS: list[tuple[str, int]] = [
        ("5 min", 5 * 60),
        ("10 min", 10 * 60),
        ("15 min", 15 * 60),
        ("25 min (Pomodoro)", 25 * 60),
        ("30 min", 30 * 60),
        ("60 min", 60 * 60),
    ]

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ChronoFlex - Timer for Windows")
        self.root.geometry("660x920")
        self.root.resizable(False, False)

        # ---- Settings ----
        self._settings = _load_settings()
        self._theme_name: str = self._settings.get("theme", "dark")
        self._apply_theme(self._theme_name)
        self.root.configure(bg=self.BG)

        # ---- State ----
        self._lock = threading.Lock()
        self.mode = tk.StringVar(value="precise")
        self.running = False
        self.paused = False
        self.total_seconds = 0
        self.remaining_seconds = 0
        self.target_end_time = 0.0
        self.timer_thread: threading.Thread | None = None
        self.alarm_playing = False
        self.alarm_thread: threading.Thread | None = None
        self.flash_state = False
        self._completed_timer_label: str = ""

        self._build_ui()
        self._draw_progress()

        # Keyboard shortcuts
        self.root.bind("<space>", lambda e: self._on_space())
        self.root.bind("<r>", lambda e: self._on_r())
        self.root.bind("<Escape>", lambda e: self._on_escape())

        # Cleanup on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # =====================================================================
    # Theme
    # =====================================================================
    def _apply_theme(self, name: str) -> None:
        theme = THEMES.get(name, THEMES["dark"])
        for key, value in theme.items():
            setattr(self, key, value)
        self._theme_name = name

    def _toggle_theme(self) -> None:
        if self.running:
            return  # Don't switch theme while timer is running
        new = "light" if self._theme_name == "dark" else "dark"
        self._apply_theme(new)
        self._settings["theme"] = new
        _save_settings(self._settings)
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Tear down and rebuild the entire UI with the new theme."""
        for widget in self.root.winfo_children():
            widget.destroy()
        self.root.configure(bg=self.BG)
        self._build_ui()
        self._draw_progress()

    # =====================================================================
    # UI
    # =====================================================================
    def _build_ui(self) -> None:
        self._build_header()
        self._build_tabs()
        self._build_presets()
        self._build_config_card()
        self._build_footer()
        self._build_controls()
        self._build_display_card()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=30, pady=(20, 0))

        title_row = tk.Frame(header, bg=self.BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text="ChronoFlex",
                 font=("Segoe UI Semibold", 24),
                 bg=self.BG, fg=self.TEXT).pack(side="left")

        # Theme toggle button
        theme_label = "Sun" if self._theme_name == "dark" else "Moon"
        self.theme_btn = tk.Button(
            title_row, text=theme_label, font=("Segoe UI", 11),
            bg=self.BG, fg=self.TEXT, relief="flat", bd=0,
            cursor="hand2", command=self._toggle_theme,
        )
        self.theme_btn.pack(side="right")
        ToolTip(self.theme_btn, "Toggle dark/light theme")

        tk.Label(header, text="Precision and random-interval timer for Windows",
                 font=("Segoe UI", 10), bg=self.BG, fg=self.MUTED).pack(anchor="w")

    def _build_tabs(self) -> None:
        tabs = tk.Frame(self.root, bg=self.BG)
        tabs.pack(fill="x", padx=30, pady=(16, 8))
        self.precise_btn = tk.Button(
            tabs, text="Precise", font=("Segoe UI Semibold", 11),
            bg=self.ACCENT, fg=self.BG, relief="flat", padx=20, pady=8,
            cursor="hand2", command=lambda: self._switch_mode("precise"))
        self.precise_btn.pack(side="left", padx=(0, 6))
        self.random_btn = tk.Button(
            tabs, text="Random", font=("Segoe UI Semibold", 11),
            bg=self.CARD, fg=self.TEXT, relief="flat", padx=20, pady=8,
            cursor="hand2", command=lambda: self._switch_mode("random"))
        self.random_btn.pack(side="left")

    def _build_presets(self) -> None:
        """Quick-start preset buttons."""
        presets_frame = tk.Frame(self.root, bg=self.BG)
        presets_frame.pack(fill="x", padx=30, pady=(0, 8))

        tk.Label(presets_frame, text="Quick start:",
                 font=("Segoe UI", 9), bg=self.BG, fg=self.MUTED
                 ).pack(side="left", padx=(0, 8))

        self.preset_buttons: list[tk.Button] = []
        for label, seconds in self.PRESETS:
            btn = tk.Button(
                presets_frame, text=label, font=("Segoe UI", 9),
                bg=self.CARD_ALT, fg=self.TEXT, relief="flat",
                padx=10, pady=4, cursor="hand2",
                command=lambda s=seconds: self._apply_preset(s),
            )
            btn.pack(side="left", padx=3)
            self.preset_buttons.append(btn)
            ToolTip(btn, f"Start a {label} timer")

    def _build_config_card(self) -> None:
        config_card = tk.Frame(self.root, bg=self.CARD)
        config_card.pack(fill="x", padx=30, pady=4)
        self.precise_panel = tk.Frame(config_card, bg=self.CARD)
        self.random_panel = tk.Frame(config_card, bg=self.CARD)
        self._build_precise_panel(self.precise_panel)
        self._build_random_panel(self.random_panel)
        self.precise_panel.pack(fill="x", padx=20, pady=16)

    def _build_footer(self) -> None:
        tk.Label(self.root,
                 text="Space = Start/Pause  |  R = Reset  |  Esc = Dismiss",
                 font=("Segoe UI", 9), bg=self.BG, fg=self.SUBTLE
                 ).pack(side="bottom", pady=(0, 6))

    def _build_controls(self) -> None:
        controls = tk.Frame(self.root, bg=self.BG)
        controls.pack(side="bottom", pady=8)
        self.start_btn = tk.Button(
            controls, text="Start", font=("Segoe UI Semibold", 12),
            bg=self.SUCCESS, fg=self.BG, relief="flat", padx=28, pady=10,
            cursor="hand2", command=self.start_timer)
        self.start_btn.pack(side="left", padx=5)
        ToolTip(self.start_btn, "Start timer (Space)")

        self.pause_btn = tk.Button(
            controls, text="Pause", font=("Segoe UI Semibold", 12),
            bg=self.WARNING, fg=self.BG, relief="flat", padx=28, pady=10,
            cursor="hand2", state="disabled", command=self.pause_timer)
        self.pause_btn.pack(side="left", padx=5)
        ToolTip(self.pause_btn, "Pause / Resume timer (Space)")

        self.reset_btn = tk.Button(
            controls, text="Reset", font=("Segoe UI Semibold", 12),
            bg=self.DANGER, fg=self.BG, relief="flat", padx=28, pady=10,
            cursor="hand2", state="disabled", command=self.reset_timer)
        self.reset_btn.pack(side="left", padx=5)
        ToolTip(self.reset_btn, "Reset timer (R)")

    def _build_display_card(self) -> None:
        display_card = tk.Frame(self.root, bg=self.CARD)
        display_card.pack(fill="both", expand=True, padx=30, pady=(8, 10))

        # Top spacer
        tk.Frame(display_card, bg=self.CARD).pack(
            side="top", fill="both", expand=True)

        # Centered content
        canvas_wrap = tk.Frame(display_card, bg=self.CARD)
        canvas_wrap.pack(side="top")

        self.canvas_size = 280
        self.canvas = tk.Canvas(canvas_wrap, width=self.canvas_size,
                                height=self.canvas_size, bg=self.CARD,
                                highlightthickness=0)
        self.canvas.pack()

        self.status_label = tk.Label(canvas_wrap, text="Ready to start",
                                     font=("Segoe UI", 11), bg=self.CARD,
                                     fg=self.MUTED)
        self.status_label.pack(pady=(0, 8))

        # Bottom spacer
        tk.Frame(display_card, bg=self.CARD).pack(
            side="top", fill="both", expand=True)

    def _build_precise_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Set Duration",
                 font=("Segoe UI Semibold", 12),
                 bg=parent["bg"], fg=self.TEXT).pack(anchor="w", pady=(0, 10))

        grid = tk.Frame(parent, bg=parent["bg"])
        grid.pack(fill="x")

        units = [("Days", "d"), ("Hours", "h"),
                 ("Minutes", "m"), ("Seconds", "s")]
        self.precise_entries: dict[str, tk.Entry] = {}

        for i, (name, key) in enumerate(units):
            cell = tk.Frame(grid, bg=parent["bg"])
            cell.grid(row=0, column=i, padx=6, sticky="ew")
            grid.grid_columnconfigure(i, weight=1)

            entry = tk.Entry(cell, font=("Segoe UI", 18), width=4,
                             justify="center", bg=self.INPUT_BG, fg=self.TEXT,
                             insertbackground=self.TEXT, relief="flat", bd=0)
            entry.insert(0, "0")
            entry.pack(pady=(0, 4))
            entry.bind("<FocusOut>", lambda e: self._sanitize_int(e.widget))

            tk.Label(cell, text=name, font=("Segoe UI", 9),
                     bg=parent["bg"], fg=self.MUTED).pack()
            self.precise_entries[key] = entry

        tk.Label(parent,
                 text="Enter any combination of units (others can stay 0).",
                 font=("Segoe UI", 9), bg=parent["bg"],
                 fg=self.SUBTLE).pack(anchor="w", pady=(10, 0))

    def _build_random_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Random Range  (1-60 minutes)",
                 font=("Segoe UI Semibold", 12),
                 bg=parent["bg"], fg=self.TEXT).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x")

        # Min
        min_cell = tk.Frame(row, bg=parent["bg"])
        min_cell.pack(side="left", padx=(0, 30))
        tk.Label(min_cell, text="Minimum (min)", font=("Segoe UI", 9),
                 bg=parent["bg"], fg=self.MUTED).pack(anchor="w")
        self.rand_min_entry = tk.Entry(min_cell, font=("Segoe UI", 18),
                                       width=5, justify="center",
                                       bg=self.INPUT_BG, fg=self.TEXT,
                                       insertbackground=self.TEXT,
                                       relief="flat", bd=0)
        self.rand_min_entry.insert(0, "5")
        self.rand_min_entry.pack(pady=(4, 0))
        self.rand_min_entry.bind("<FocusOut>",
                                 lambda e: self._sanitize_int(e.widget))

        # Arrow
        tk.Label(row, text="->", font=("Segoe UI", 18),
                 bg=parent["bg"], fg=self.MUTED).pack(side="left", padx=10)

        # Max
        max_cell = tk.Frame(row, bg=parent["bg"])
        max_cell.pack(side="left", padx=(30, 0))
        tk.Label(max_cell, text="Maximum (min)", font=("Segoe UI", 9),
                 bg=parent["bg"], fg=self.MUTED).pack(anchor="w")
        self.rand_max_entry = tk.Entry(max_cell, font=("Segoe UI", 18),
                                       width=5, justify="center",
                                       bg=self.INPUT_BG, fg=self.TEXT,
                                       insertbackground=self.TEXT,
                                       relief="flat", bd=0)
        self.rand_max_entry.insert(0, "30")
        self.rand_max_entry.pack(pady=(4, 0))
        self.rand_max_entry.bind("<FocusOut>",
                                 lambda e: self._sanitize_int(e.widget))

        tk.Label(parent,
                 text=("A random value will be picked within your range when "
                       "you press Start.\nValues are clamped to 1-60 minutes."),
                 font=("Segoe UI", 9), bg=parent["bg"],
                 fg=self.SUBTLE, justify="left").pack(anchor="w", pady=(12, 0))

    # =====================================================================
    # Presets
    # =====================================================================
    def _apply_preset(self, seconds: int) -> None:
        """Apply a preset duration: switch to precise mode and fill entries."""
        if self.running and not self.paused:
            return
        self._switch_mode("precise")
        d, remainder = divmod(seconds, 86400)
        h, remainder = divmod(remainder, 3600)
        m, s = divmod(remainder, 60)
        self.precise_entries["d"].delete(0, "end")
        self.precise_entries["d"].insert(0, str(d))
        self.precise_entries["h"].delete(0, "end")
        self.precise_entries["h"].insert(0, str(h))
        self.precise_entries["m"].delete(0, "end")
        self.precise_entries["m"].insert(0, str(m))
        self.precise_entries["s"].delete(0, "end")
        self.precise_entries["s"].insert(0, str(s))
        self.status_label.configure(text=f"Preset: {self._format_time(seconds)}")

    # =====================================================================
    # Keyboard shortcuts
    # =====================================================================
    def _is_entry_focused(self) -> bool:
        """Return True if an Entry widget currently has keyboard focus."""
        focused = self.root.focus_get()
        return isinstance(focused, tk.Entry)

    def _on_space(self) -> None:
        if self._is_entry_focused():
            return
        if self.alarm_playing:
            self.dismiss_alarm()
        elif self.running and not self.paused:
            self.pause_timer()
        elif self.running and self.paused:
            self.resume_timer()
        else:
            self.start_timer()

    def _on_r(self) -> None:
        if self._is_entry_focused():
            return
        if self.alarm_playing:
            self.dismiss_alarm()
        else:
            self.reset_timer()

    def _on_escape(self) -> None:
        if self._is_entry_focused():
            return
        if self.alarm_playing:
            self.dismiss_alarm()

    # =====================================================================
    # Helpers
    # =====================================================================
    def _sanitize_int(self, widget: tk.Entry) -> None:
        val = widget.get().strip()
        try:
            int(val)
        except ValueError:
            widget.delete(0, "end")
            widget.insert(0, "0")

    def _format_time(self, seconds: int | float) -> str:
        seconds = int(seconds)
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if d > 0:
            return f"{d}d {h:02d}:{m:02d}:{s:02d}"
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _get_precise_seconds(self) -> int:
        total = 0
        for key, entry in self.precise_entries.items():
            raw = entry.get().strip()
            try:
                v = int(raw) if raw else 0
            except ValueError:
                v = 0
            if v < 0:
                v = 0
            if key == "d":
                total += v * 86400
            elif key == "h":
                total += v * 3600
            elif key == "m":
                total += v * 60
            elif key == "s":
                total += v
        return total

    def _clamp_random_range(self) -> tuple[int, int]:
        """Clamp and normalize the random range entries, returning (lo, hi)."""
        try:
            lo = int(self.rand_min_entry.get().strip() or "1")
            hi = int(self.rand_max_entry.get().strip() or "60")
        except ValueError:
            raise InvalidRangeError("Please enter valid integers.")
        lo = max(1, min(60, lo))
        hi = max(1, min(60, hi))
        if lo > hi:
            lo, hi = hi, lo
        self.rand_min_entry.delete(0, "end")
        self.rand_min_entry.insert(0, str(lo))
        self.rand_max_entry.delete(0, "end")
        self.rand_max_entry.insert(0, str(hi))
        return lo, hi

    def _get_random_seconds(self, lo: int, hi: int) -> tuple[int, str]:
        """Return (seconds, info_message) for a random pick in [lo, hi] min."""
        chosen = random.randint(lo, hi)
        return chosen * 60, f"Random pick: {chosen} minute{'s' if chosen != 1 else ''} ({lo}-{hi})"

    def _set_inputs_state(self, state: Literal["normal", "disabled", "readonly"]) -> None:
        for entry in self.precise_entries.values():
            entry.configure(state=state)
        self.rand_min_entry.configure(state=state)
        self.rand_max_entry.configure(state=state)

    # =====================================================================
    # History
    # =====================================================================
    def _log_history(self, label: str, seconds: int) -> None:
        """Append a completed timer to the history file."""
        history = _load_history()
        history.insert(0, {
            "label": label,
            "seconds": seconds,
            "completed_at": datetime.now().isoformat(),
        })
        # Keep last 50 entries
        _save_history(history[:50])

    # =====================================================================
    # Mode switching
    # =====================================================================
    def _switch_mode(self, mode: str) -> None:
        if self.running and not self.paused:
            return
        self.mode.set(mode)
        if mode == "precise":
            self.precise_btn.configure(bg=self.ACCENT, fg=self.BG)
            self.random_btn.configure(bg=self.CARD, fg=self.TEXT)
            self.random_panel.pack_forget()
            self.precise_panel.pack(fill="x", padx=20, pady=16)
        else:
            self.random_btn.configure(bg=self.ACCENT, fg=self.BG)
            self.precise_btn.configure(bg=self.CARD, fg=self.TEXT)
            self.precise_panel.pack_forget()
            self.random_panel.pack(fill="x", padx=20, pady=16)
        self.reset_timer()

    # =====================================================================
    # Timer control
    # =====================================================================
    def start_timer(self) -> None:
        if self.running and not self.paused:
            return

        if not self.running:
            # Fresh start
            if self.mode.get() == "precise":
                total = self._get_precise_seconds()
                if total <= 0:
                    messagebox.showwarning(
                        "No duration",
                        "Please enter a duration greater than zero.")
                    return
                self.total_seconds = total
                self._completed_timer_label = self._format_time(total)
                self.status_label.configure(
                    text=f"Timer set for {self._format_time(total)}")
            else:
                try:
                    lo, hi = self._clamp_random_range()
                except InvalidRangeError as e:
                    messagebox.showwarning("Invalid range", str(e))
                    return
                total, msg = self._get_random_seconds(lo, hi)
                self.total_seconds = total
                self._completed_timer_label = msg
                self.status_label.configure(text=msg)

            self.remaining_seconds = self.total_seconds
            self.target_end_time = time.time() + self.total_seconds
        else:
            # Resume from pause
            with self._lock:
                self.target_end_time = time.time() + self.remaining_seconds
            self.status_label.configure(text="Resumed")

        self.running = True
        self.paused = False
        self.alarm_playing = False

        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause",
                                 command=self.pause_timer)
        self.reset_btn.configure(state="normal")
        self._set_inputs_state("disabled")

        self.timer_thread = threading.Thread(target=self._run_countdown,
                                             daemon=True)
        self.timer_thread.start()

    def _run_countdown(self) -> None:
        try:
            while self.running:
                if self.paused:
                    time.sleep(self._TICK_INTERVAL)
                    continue
                now = time.time()
                with self._lock:
                    self.remaining_seconds = max(
                        0, int(self.target_end_time - now + self._ROUNDING_OFFSET))
                    remaining = self.remaining_seconds
                self.root.after(0, self._draw_progress)
                if remaining <= 0:
                    self.root.after(0, self._on_complete)
                    break
                time.sleep(self._TICK_INTERVAL)
        except Exception:
            logger.exception("Timer thread crashed")
            self.root.after(0, lambda: self.status_label.configure(text="Timer error"))

    def pause_timer(self) -> None:
        if not self.running or self.paused:
            return
        self.paused = True
        self.status_label.configure(text="Paused")
        self.pause_btn.configure(text="Resume", command=self.resume_timer)
        self._draw_progress()

    def resume_timer(self) -> None:
        if not self.paused:
            return
        self.paused = False
        self.target_end_time = time.time() + self.remaining_seconds
        self.status_label.configure(text="Running")
        self.pause_btn.configure(text="Pause", command=self.pause_timer)
        self._draw_progress()

    def reset_timer(self) -> None:
        self.running = False
        self.paused = False
        self.alarm_playing = False
        with self._lock:
            self.remaining_seconds = 0
        self.total_seconds = 0

        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Pause",
                                 command=self.pause_timer)
        self.reset_btn.configure(state="disabled", text="Reset")
        self.status_label.configure(text="Ready to start")
        self._set_inputs_state("normal")
        self._draw_progress()

    # =====================================================================
    # Completion / alarm
    # =====================================================================
    def _on_complete(self) -> None:
        self.running = False
        self.alarm_playing = True
        with self._lock:
            self.remaining_seconds = 0
        self.status_label.configure(text="Time's up!")

        self.pause_btn.configure(state="disabled", text="Pause",
                                 command=self.pause_timer)
        self.start_btn.configure(state="disabled")
        self.reset_btn.configure(state="normal", text="Dismiss",
                                 command=self.dismiss_alarm)

        # Log to history
        self._log_history(self._completed_timer_label, self.total_seconds)

        # Toast notification
        _send_toast("ChronoFlex", f"Timer finished: {self._completed_timer_label}")

        self.alarm_thread = threading.Thread(target=self._play_alarm,
                                             daemon=True)
        self.alarm_thread.start()
        self._flash_alarm()

        # Bring window to front
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(self._TOPMOST_DURATION_MS,
                        lambda: self.root.attributes("-topmost", False))

    def _play_alarm(self) -> None:
        try:
            while self.alarm_playing:
                for _ in range(self._BEEPS_PER_GROUP):
                    if not self.alarm_playing:
                        break
                    winsound.Beep(self._BEEP_FREQUENCY, self._BEEP_DURATION_MS)
                    time.sleep(self._BEEP_PAUSE)
                time.sleep(self._BEEP_GROUP_PAUSE)
        except Exception:
            logger.exception("Alarm thread crashed")
            self.root.after(0, lambda: self.status_label.configure(text="Alarm error"))

    def _flash_alarm(self) -> None:
        if not self.alarm_playing:
            self._draw_progress()
            return
        self.flash_state = not self.flash_state
        self._draw_progress()
        self.root.after(self._FLASH_INTERVAL_MS, self._flash_alarm)

    def dismiss_alarm(self) -> None:
        self.reset_btn.configure(text="Reset")
        self.reset_timer()

    # =====================================================================
    # Drawing
    # =====================================================================
    def _draw_progress(self) -> None:
        self.canvas.delete("all")
        size = self.canvas_size
        center = size // 2
        radius = size // 2 - 18
        ring_width = 12

        # Background ring
        self.canvas.create_oval(center - radius, center - radius,
                                center + radius, center + radius,
                                outline=self.INPUT_BG, width=ring_width)

        with self._lock:
            remaining = self.remaining_seconds

        progress = (remaining / self.total_seconds
                    if self.total_seconds > 0 else 0)

        # Adaptive color
        if self.alarm_playing:
            arc_color = self.DANGER if self.flash_state else self.WARNING
        elif not self.running:
            arc_color = self.MUTED
        elif remaining < 60:
            arc_color = self.DANGER
        elif remaining < 300:
            arc_color = self.WARNING
        else:
            arc_color = self.ACCENT

        if progress > 0:
            angle = 360 * progress
            self.canvas.create_arc(center - radius, center - radius,
                                   center + radius, center + radius,
                                   start=90, extent=-angle, style="arc",
                                   outline=arc_color, width=ring_width)

        # Center text
        time_str = self._format_time(remaining)
        self.canvas.create_text(center, center - 10, text=time_str,
                                font=("Segoe UI Semibold", 32), fill=self.TEXT)

        if self.alarm_playing:
            label_text = "TIME'S UP!"
        elif self.paused:
            label_text = "PAUSED"
        elif self.running:
            label_text = "REMAINING"
        else:
            label_text = "READY"
        self.canvas.create_text(center, center + 24, text=label_text,
                                font=("Segoe UI", 10), fill=self.MUTED)

    # =====================================================================
    # Close handler
    # =====================================================================
    def _on_close(self) -> None:
        self.alarm_playing = False
        self.running = False
        # Save settings on close
        _save_settings(self._settings)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = ChronoFlex(root)

    # Center on screen
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"+{x}+{y}")

    root.mainloop()


if __name__ == "__main__":
    main()
