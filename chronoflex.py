"""
ChronoFlex — A Windows timer application.

Features
--------
* Precise mode: set duration in days / hours / minutes / seconds.
* Random mode: pick a random duration inside a user-defined range
  (clamped to the 1–60 minute window).
* Circular progress ring with adaptive color (blue → amber → red).
* Start / Pause / Resume / Reset.
* Flashing visual + repeated beep alarm on completion.
* Brings itself to the front when the alarm fires.
"""

import tkinter as tk
from tkinter import messagebox
from typing import Literal
import logging
import random
import time
import threading
import winsound

logger = logging.getLogger(__name__)


class InvalidRangeError(ValueError):
    """Raised when the random range inputs are invalid."""


class ChronoFlex:
    # ---- Catppuccin-Mocha inspired palette ----
    BG          = "#1e1e2e"
    CARD        = "#313244"
    CARD_ALT    = "#2a2a3c"
    INPUT_BG    = "#45475a"
    ACCENT      = "#89b4fa"
    ACCENT_HOV  = "#b4d0fb"
    TEXT        = "#cdd6f4"
    MUTED       = "#a6adc8"
    SUBTLE      = "#6c7086"
    DANGER      = "#f38ba8"
    SUCCESS     = "#a6e3a1"
    WARNING     = "#f9e2af"

    # ---- Timing / alarm constants ----
    # Timer polling interval (seconds)
    _TICK_INTERVAL: float = 0.1
    # Rounding offset so "1 second remaining" displays for a full second
    _ROUNDING_OFFSET: float = 0.999
    # Milliseconds to keep window topmost after alarm fires
    _TOPMOST_DURATION_MS: int = 2500
    # Flash toggle interval (ms)
    _FLASH_INTERVAL_MS: int = 400
    # Alarm beep frequency (Hz) and duration (ms)
    _BEEP_FREQUENCY: int = 880
    _BEEP_DURATION_MS: int = 200
    # Pause between individual beeps (seconds)
    _BEEP_PAUSE: float = 0.05
    # Pause between beep groups (seconds)
    _BEEP_GROUP_PAUSE: float = 0.4
    # Number of beeps per alarm group
    _BEEPS_PER_GROUP: int = 3

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ChronoFlex — Timer for Windows")
        self.root.geometry("640x820")  # Slightly taller to guarantee buttons fit
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)

        # ---- State ----
        self._lock = threading.Lock()
        self.mode = tk.StringVar(value="precise")
        self.running = False
        self.paused = False
        self.total_seconds = 0
        self.remaining_seconds = 0
        self.target_end_time = 0.0
        self.timer_thread = None
        self.alarm_playing = False
        self.alarm_thread = None
        self.flash_state = False

        self._build_ui()
        self._draw_progress()

        # Cleanup on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # =====================================================================
    # UI
    # =====================================================================
    def _build_ui(self) -> None:
        self._build_header()
        self._build_tabs()
        self._build_config_card()
        self._build_footer()
        self._build_controls()
        self._build_display_card()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=30, pady=(24, 0))
        tk.Label(header, text="⏱  ChronoFlex",
                 font=("Segoe UI Semibold", 26),
                 bg=self.BG, fg=self.TEXT).pack(anchor="w")
        tk.Label(header, text="Precision and random-interval timer for Windows",
                 font=("Segoe UI", 10), bg=self.BG, fg=self.MUTED).pack(anchor="w")

    def _build_tabs(self) -> None:
        tabs = tk.Frame(self.root, bg=self.BG)
        tabs.pack(fill="x", padx=30, pady=(20, 10))
        self.precise_btn = tk.Button(
            tabs, text="🎯  Precise", font=("Segoe UI Semibold", 11),
            bg=self.ACCENT, fg=self.BG, relief="flat", padx=20, pady=8,
            cursor="hand2", command=lambda: self._switch_mode("precise"))
        self.precise_btn.pack(side="left", padx=(0, 6))
        self.random_btn = tk.Button(
            tabs, text="🎲  Random", font=("Segoe UI Semibold", 11),
            bg=self.CARD, fg=self.TEXT, relief="flat", padx=20, pady=8,
            cursor="hand2", command=lambda: self._switch_mode("random"))
        self.random_btn.pack(side="left")

    def _build_config_card(self) -> None:
        config_card = tk.Frame(self.root, bg=self.CARD)
        config_card.pack(fill="x", padx=30, pady=6)
        self.precise_panel = tk.Frame(config_card, bg=self.CARD)
        self.random_panel  = tk.Frame(config_card, bg=self.CARD)
        self._build_precise_panel(self.precise_panel)
        self._build_random_panel(self.random_panel)
        self.precise_panel.pack(fill="x", padx=20, pady=20)

    def _build_footer(self) -> None:
        tk.Label(self.root,
                 text="Press Start to begin  •  Alarm will sound when finished",
                 font=("Segoe UI", 9), bg=self.BG, fg=self.SUBTLE
                 ).pack(side="bottom", pady=10)

    def _build_controls(self) -> None:
        controls = tk.Frame(self.root, bg=self.BG)
        controls.pack(side="bottom", pady=10)
        self.start_btn = tk.Button(
            controls, text="▶  Start", font=("Segoe UI Semibold", 12),
            bg=self.SUCCESS, fg=self.BG, relief="flat", padx=30, pady=12,
            cursor="hand2", command=self.start_timer)
        self.start_btn.pack(side="left", padx=6)
        self.pause_btn = tk.Button(
            controls, text="⏸  Pause", font=("Segoe UI Semibold", 12),
            bg=self.WARNING, fg=self.BG, relief="flat", padx=30, pady=12,
            cursor="hand2", state="disabled", command=self.pause_timer)
        self.pause_btn.pack(side="left", padx=6)
        self.reset_btn = tk.Button(
            controls, text="⏹  Reset", font=("Segoe UI Semibold", 12),
            bg=self.DANGER, fg=self.BG, relief="flat", padx=30, pady=12,
            cursor="hand2", state="disabled", command=self.reset_timer)
        self.reset_btn.pack(side="left", padx=6)

    def _build_display_card(self) -> None:
        display_card = tk.Frame(self.root, bg=self.CARD)
        display_card.pack(fill="both", expand=True, padx=30, pady=10)
        canvas_wrap = tk.Frame(display_card, bg=self.CARD)
        canvas_wrap.pack(pady=(20, 10))
        self.canvas_size = 320
        self.canvas = tk.Canvas(canvas_wrap, width=self.canvas_size,
                                height=self.canvas_size, bg=self.CARD,
                                highlightthickness=0)
        self.canvas.pack()
        self.status_label = tk.Label(display_card, text="Ready to start",
                                     font=("Segoe UI", 11), bg=self.CARD,
                                     fg=self.MUTED)
        self.status_label.pack(pady=(0, 20))

    def _build_precise_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Set Duration",
                 font=("Segoe UI Semibold", 12),
                 bg=parent["bg"], fg=self.TEXT).pack(anchor="w", pady=(0, 12))

        grid = tk.Frame(parent, bg=parent["bg"])
        grid.pack(fill="x")

        units = [("Days", "d"), ("Hours", "h"),
                 ("Minutes", "m"), ("Seconds", "s")]
        self.precise_entries = {}

        for i, (name, key) in enumerate(units):
            cell = tk.Frame(grid, bg=parent["bg"])
            cell.grid(row=0, column=i, padx=8, sticky="ew")
            grid.grid_columnconfigure(i, weight=1)

            entry = tk.Entry(cell, font=("Segoe UI", 20), width=4,
                             justify="center", bg=self.INPUT_BG, fg=self.TEXT,
                             insertbackground=self.TEXT, relief="flat", bd=0)
            entry.insert(0, "0")
            entry.pack(pady=(0, 6))
            entry.bind("<FocusOut>", lambda e: self._sanitize_int(e.widget))

            tk.Label(cell, text=name, font=("Segoe UI", 9),
                     bg=parent["bg"], fg=self.MUTED).pack()
            self.precise_entries[key] = entry

        tk.Label(parent,
                 text="Enter any combination of units (others can stay 0).",
                 font=("Segoe UI", 9), bg=parent["bg"],
                 fg=self.SUBTLE).pack(anchor="w", pady=(12, 0))

    def _build_random_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="Random Range  (1–60 minutes)",
                 font=("Segoe UI Semibold", 12),
                 bg=parent["bg"], fg=self.TEXT).pack(anchor="w", pady=(0, 12))

        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x")

        # Min
        min_cell = tk.Frame(row, bg=parent["bg"])
        min_cell.pack(side="left", padx=(0, 30))
        tk.Label(min_cell, text="Minimum (min)", font=("Segoe UI", 9),
                 bg=parent["bg"], fg=self.MUTED).pack(anchor="w")
        self.rand_min_entry = tk.Entry(min_cell, font=("Segoe UI", 20),
                                       width=5, justify="center",
                                       bg=self.INPUT_BG, fg=self.TEXT,
                                       insertbackground=self.TEXT,
                                       relief="flat", bd=0)
        self.rand_min_entry.insert(0, "5")
        self.rand_min_entry.pack(pady=(4, 0))
        self.rand_min_entry.bind("<FocusOut>",
                                 lambda e: self._sanitize_int(e.widget))

        # Arrow
        tk.Label(row, text="→", font=("Segoe UI", 20),
                 bg=parent["bg"], fg=self.MUTED).pack(side="left", padx=10)

        # Max
        max_cell = tk.Frame(row, bg=parent["bg"])
        max_cell.pack(side="left", padx=(30, 0))
        tk.Label(max_cell, text="Maximum (min)", font=("Segoe UI", 9),
                 bg=parent["bg"], fg=self.MUTED).pack(anchor="w")
        self.rand_max_entry = tk.Entry(max_cell, font=("Segoe UI", 20),
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
                       "you press Start.\nValues are clamped to 1–60 minutes."),
                 font=("Segoe UI", 9), bg=parent["bg"],
                 fg=self.SUBTLE, justify="left").pack(anchor="w", pady=(16, 0))

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
            if key == "d": total += v * 86400
            elif key == "h": total += v * 3600
            elif key == "m": total += v * 60
            elif key == "s": total += v
        return total

    def _clamp_random_range(self) -> tuple[int, int]:
        """Clamp and normalize the random range entries, returning (lo, hi) in minutes.

        Updates the entry widgets to reflect the clamped values.

        Raises
        ------
        InvalidRangeError
            If the min/max entries are not valid integers.
        """
        try:
            lo = int(self.rand_min_entry.get().strip() or "1")
            hi = int(self.rand_max_entry.get().strip() or "60")
        except ValueError:
            raise InvalidRangeError("Please enter valid integers for the random range.")
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
        """Return (seconds, info_message) for a random pick in ``[lo, hi]`` minutes.

        This is a pure computation — it does NOT modify any UI widgets.
        """
        chosen = random.randint(lo, hi)
        return chosen * 60, f"Random pick: {chosen} minute{'s' if chosen != 1 else ''} ({lo}–{hi})"

    def _set_inputs_state(self, state: Literal["normal", "disabled", "readonly"]) -> None:
        for entry in self.precise_entries.values():
            entry.configure(state=state)
        self.rand_min_entry.configure(state=state)
        self.rand_max_entry.configure(state=state)

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
            self.precise_panel.pack(fill="x", padx=20, pady=20)
        else:
            self.random_btn.configure(bg=self.ACCENT, fg=self.BG)
            self.precise_btn.configure(bg=self.CARD, fg=self.TEXT)
            self.precise_panel.pack_forget()
            self.random_panel.pack(fill="x", padx=20, pady=20)
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
        self.pause_btn.configure(state="normal", text="⏸  Pause",
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
                    self.remaining_seconds = max(0, int(self.target_end_time - now + self._ROUNDING_OFFSET))
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
        self.pause_btn.configure(text="▶  Resume", command=self.resume_timer)
        self._draw_progress()

    def resume_timer(self) -> None:
        if not self.paused:
            return
        self.paused = False
        self.target_end_time = time.time() + self.remaining_seconds
        self.status_label.configure(text="Running")
        self.pause_btn.configure(text="⏸  Pause", command=self.pause_timer)
        self._draw_progress()

    def reset_timer(self) -> None:
        self.running = False
        self.paused = False
        self.alarm_playing = False
        with self._lock:
            self.remaining_seconds = 0
        self.total_seconds = 0

        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="⏸  Pause",
                                 command=self.pause_timer)
        self.reset_btn.configure(state="disabled", text="⏹  Reset")
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
        self.status_label.configure(text="⏰ Time's up!")

        self.pause_btn.configure(state="disabled", text="⏸  Pause",
                                 command=self.pause_timer)
        self.start_btn.configure(state="disabled")
        self.reset_btn.configure(state="normal", text="✓  Dismiss",
                                 command=self.dismiss_alarm)

        self.alarm_thread = threading.Thread(target=self._play_alarm,
                                             daemon=True)
        self.alarm_thread.start()
        self._flash_alarm()

        # Bring window to front
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(self._TOPMOST_DURATION_MS, lambda: self.root.attributes("-topmost", False))

    def _play_alarm(self) -> None:
        # Short beeps, brief pause, repeat until dismissed
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
        self.reset_btn.configure(text="⏹  Reset")
        self.reset_timer()

    # =====================================================================
    # Drawing
    # =====================================================================
    def _draw_progress(self) -> None:
        self.canvas.delete("all")
        size = self.canvas_size
        center = size // 2
        radius = size // 2 - 30
        ring_width = 14

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
        self.canvas.create_text(center, center - 12, text=time_str,
                                font=("Segoe UI Semibold", 34), fill=self.TEXT)

        if self.alarm_playing:
            label_text = "TIME'S UP!"
        elif self.paused:
            label_text = "PAUSED"
        elif self.running:
            label_text = "REMAINING"
        else:
            label_text = "READY"
        self.canvas.create_text(center, center + 28, text=label_text,
                                font=("Segoe UI", 10), fill=self.MUTED)

    # =====================================================================
    # Close handler
    # =====================================================================
    def _on_close(self) -> None:
        self.alarm_playing = False
        self.running = False
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