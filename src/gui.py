"""
R2D2 Joystick Monitor — GUI

A real-time graphical monitor for EdgeTX radio channel output.
Supports two connection modes with automatic detection:

  - USB-VCP (Serial): Reads data from the R2D2 Lua script
  - USB HID Joystick:  Reads standard gamepad axes/buttons

Features:
  - Auto-detects BumbleBee in either mode and connects automatically
  - Two 2D stick position displays (left/right gimbal)
  - Animated channel bars for all 24 channels
  - Raw value readout
  - Auto-reconnect on disconnect
"""

import threading
import time
import tkinter as tk
from tkinter import ttk
import serial
import serial.tools.list_ports

from src.hid_reader import (
    HAS_PYGAME,
    HidReader,
    detect_hid_bumblebee,
    init_pygame_joystick,
)


# ── Defaults ──────────────────────────────────────────────
DEFAULT_BAUDRATE = 115200
CHANNEL_COUNT = 32
UPDATE_MS = 16  # ~60 fps GUI refresh
SCAN_INTERVAL_FRAMES = 60  # Auto-detect every ~1 second

BUMBLEBEE_SERIAL_KEYWORDS = ["bumblebee"]


# ── Colors (dark theme) ──────────────────────────────────
BG           = "#0f1117"
BG_PANEL     = "#181b24"
BG_CARD      = "#1e2230"
BORDER       = "#2a2e3d"
TEXT         = "#e0e4f0"
TEXT_DIM     = "#6b7294"
ACCENT       = "#6c8cff"
ACCENT_GLOW  = "#4a6adf"
BAR_BG       = "#252a3a"
BAR_POS      = "#5b9aff"
BAR_NEG      = "#ff6b6b"
BAR_ZERO     = "#3a3f52"
STICK_BG     = "#14171f"
STICK_GRID   = "#252a3a"
STICK_DOT    = "#6c8cff"
STICK_TRAIL  = "#3a4a7a"
GREEN        = "#4ade80"
RED          = "#f87171"
YELLOW       = "#fbbf24"
ORANGE       = "#fb923c"
CYAN         = "#22d3ee"

# Mode badge colors
VCP_COLOR    = "#6c8cff"
HID_COLOR    = "#4ade80"


# ── Detection helpers ─────────────────────────────────────
def detect_serial_bumblebee() -> str | None:
    """
    Scan serial ports for a BumbleBee in USB-VCP mode.
    Returns the port device path, or None if not found.
    """
    for port in serial.tools.list_ports.comports():
        desc = (port.description or "").lower()
        if any(kw in desc for kw in BUMBLEBEE_SERIAL_KEYWORDS):
            return port.device
    return None


# ── Serial parsing ────────────────────────────────────────
def parse_r2d2_line(line: str) -> list[int] | None:
    line = line.strip()
    if not line.startswith("$"):
        return None
    payload = line[1:]
    parts = [p.strip() for p in payload.split(",") if p.strip()]
    if not parts:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


# ── Serial reader thread ─────────────────────────────────
class SerialReader:
    """Reads serial data in a background thread and stores latest channel values."""

    def __init__(self):
        self.channels: list[int] = [0] * CHANNEL_COUNT
        self.lock = threading.Lock()
        self.connected = False
        self.running = False
        self.frame_count = 0
        self.fps = 0.0
        self.error_msg = ""
        self.mode = "serial"
        self.device_name = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, port: str, baudrate: int):
        self.stop()
        self._stop_event.clear()
        self.error_msg = ""
        self.frame_count = 0
        self.running = True
        self.device_name = port
        self._thread = threading.Thread(
            target=self._read_loop, args=(port, baudrate), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.connected = False
        self.device_name = ""

    def _read_loop(self, port: str, baudrate: int):
        try:
            with serial.Serial(port, baudrate=baudrate, timeout=0.05) as ser:
                ser.reset_input_buffer()
                self.connected = True
                fps_counter = 0
                fps_time = time.time()

                while not self._stop_event.is_set():
                    # Blocking readline (returns on \n or timeout)
                    raw_line = ser.readline()
                    if raw_line:
                        line = raw_line.decode("utf-8", errors="ignore")
                        channels = parse_r2d2_line(line)
                        if channels is not None:
                            while len(channels) < CHANNEL_COUNT:
                                channels.append(0)
                            with self.lock:
                                self.channels = channels[:CHANNEL_COUNT]
                            self.frame_count += 1
                            fps_counter += 1

                    # FPS calculation
                    now = time.time()
                    if now - fps_time >= 1.0:
                        self.fps = fps_counter / (now - fps_time)
                        fps_counter = 0
                        fps_time = now

        except serial.SerialException as e:
            self.error_msg = str(e)
        finally:
            self.connected = False
            self.running = False

    def get_channels(self) -> list[int]:
        with self.lock:
            return list(self.channels)


# ── GUI Components ────────────────────────────────────────
class StickCanvas(tk.Canvas):
    """A 2D stick position visualizer with trail effect."""

    def __init__(self, parent, label: str, **kwargs):
        super().__init__(
            parent, bg=STICK_BG, highlightthickness=0,
            width=180, height=180, **kwargs
        )
        self.label = label
        self.trail: list[tuple[float, float]] = []
        self.max_trail = 12
        self.x_norm = 0.0
        self.y_norm = 0.0

    def update_position(self, x_norm: float, y_norm: float):
        """Update stick position. Values in -1.0 to +1.0 range."""
        self.x_norm = max(-1.0, min(1.0, x_norm))
        self.y_norm = max(-1.0, min(1.0, y_norm))
        self.trail.append((self.x_norm, self.y_norm))
        if len(self.trail) > self.max_trail:
            self.trail.pop(0)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        pad = 16
        area_w = w - 2 * pad
        area_h = h - 2 * pad
        cx = w // 2
        cy = h // 2

        # Background rounded rect
        self._rounded_rect(4, 4, w - 4, h - 4, 12, STICK_BG, BORDER)

        # Grid lines
        self.create_line(cx, pad, cx, h - pad, fill=STICK_GRID, width=1)
        self.create_line(pad, cy, w - pad, cy, fill=STICK_GRID, width=1)

        # Subtle circle boundary
        r = min(area_w, area_h) // 2
        self.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            outline=STICK_GRID, width=1
        )

        # Trail dots (fading)
        for i, (tx, ty) in enumerate(self.trail):
            alpha = (i + 1) / len(self.trail) if self.trail else 1.0
            px = cx + tx * (area_w // 2)
            py = cy - ty * (area_h // 2)  # Y inverted for display
            dot_r = 2 + alpha * 2
            color = self._lerp_color(STICK_BG, STICK_TRAIL, alpha * 0.7)
            self.create_oval(
                px - dot_r, py - dot_r, px + dot_r, py + dot_r,
                fill=color, outline=""
            )

        # Main position dot
        px = cx + self.x_norm * (area_w // 2)
        py = cy - self.y_norm * (area_h // 2)
        dot_r = 7

        # Glow
        for gr in range(3, 0, -1):
            glow_r = dot_r + gr * 3
            glow_color = self._lerp_color(STICK_BG, ACCENT_GLOW, 0.15 / gr)
            self.create_oval(
                px - glow_r, py - glow_r, px + glow_r, py + glow_r,
                fill=glow_color, outline=""
            )

        self.create_oval(
            px - dot_r, py - dot_r, px + dot_r, py + dot_r,
            fill=STICK_DOT, outline="#8aa8ff", width=1
        )

        # Label
        self.create_text(
            pad + 4, pad + 2, text=self.label,
            fill=TEXT_DIM, font=("Inter", 10), anchor="nw"
        )

        # Value readout
        self.create_text(
            w - pad - 4, h - pad - 2,
            text=f"({self.x_norm:+.2f}, {self.y_norm:+.2f})",
            fill=TEXT_DIM, font=("SF Mono", 9), anchor="se"
        )

    def _rounded_rect(self, x1, y1, x2, y2, r, fill, outline):
        self.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90,
                       fill=fill, outline=outline, style="pieslice")
        self.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90,
                       fill=fill, outline=outline, style="pieslice")
        self.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90,
                       fill=fill, outline=outline, style="pieslice")
        self.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90,
                       fill=fill, outline=outline, style="pieslice")
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
        self.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="")

    @staticmethod
    def _lerp_color(c1: str, c2: str, t: float) -> str:
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"


class ChannelBar(tk.Canvas):
    """A single horizontal channel bar with label and value."""

    HEIGHT = 28

    def __init__(self, parent, ch_num: int, **kwargs):
        super().__init__(
            parent, bg=BG_CARD, highlightthickness=0,
            height=self.HEIGHT, **kwargs
        )
        self.ch_num = ch_num
        self.raw_value = 0
        self.norm_value = 0.0

    def update_value(self, raw: int):
        self.raw_value = raw
        self.norm_value = max(-1.0, min(1.0, raw / 1024.0))
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.HEIGHT
        if w < 20:
            return

        label_w = 42
        value_w = 52
        bar_x = label_w + 4
        bar_end = w - value_w - 4
        bar_w = bar_end - bar_x
        bar_y = 6
        bar_h = h - 12
        bar_mid = bar_x + bar_w // 2

        # Channel label
        self.create_text(
            label_w - 4, h // 2,
            text=f"CH{self.ch_num:02d}",
            fill=TEXT_DIM, font=("SF Mono", 10), anchor="e"
        )

        # Bar background
        self.create_rectangle(
            bar_x, bar_y, bar_end, bar_y + bar_h,
            fill=BAR_BG, outline=""
        )

        # Center tick
        self.create_line(
            bar_mid, bar_y, bar_mid, bar_y + bar_h,
            fill=BAR_ZERO, width=1
        )

        # Value fill
        if self.norm_value >= 0:
            fill_x1 = bar_mid
            fill_x2 = bar_mid + int(self.norm_value * (bar_w // 2))
            color = BAR_POS
        else:
            fill_x2 = bar_mid
            fill_x1 = bar_mid + int(self.norm_value * (bar_w // 2))
            color = BAR_NEG

        if fill_x2 > fill_x1:
            self.create_rectangle(
                fill_x1, bar_y + 1, fill_x2, bar_y + bar_h - 1,
                fill=color, outline=""
            )

        # Raw value readout
        self.create_text(
            w - 4, h // 2,
            text=f"{self.raw_value:+5d}",
            fill=TEXT, font=("SF Mono", 10), anchor="e"
        )


# ── Main Application ─────────────────────────────────────
class JoystickMonitorApp:
    """Main application window with auto-detection of BumbleBee connection mode."""

    # App states
    STATE_SCANNING = "scanning"
    STATE_CONNECTING = "connecting"
    STATE_CONNECTED = "connected"
    STATE_PAUSED = "paused"  # User manually disconnected

    def __init__(self):
        # Initialize pygame for HID joystick support
        self._hid_available = False
        if HAS_PYGAME:
            self._hid_available = init_pygame_joystick()

        self.root = tk.Tk()
        self.root.title("Joystick Monitor")
        self.root.configure(bg=BG)
        self.root.minsize(720, 620)
        self.root.geometry("820x740")

        # State
        self.state = self.STATE_SCANNING
        self.active_reader = None   # SerialReader or HidReader
        self.active_mode = None     # "serial" or "hid"
        self.device_info = ""       # Port path or joystick name
        self._scan_tick = 0
        self._scan_dots = 0

        self._build_ui()
        self._schedule_update()

    # ── UI Construction ───────────────────────────────────
    def _build_ui(self):
        # ── Top bar ───────────────────────────────────────
        top = tk.Frame(self.root, bg=BG_PANEL, padx=12, pady=8)
        top.pack(fill="x")

        tk.Label(
            top, text="JOYSTICK MONITOR", bg=BG_PANEL, fg=ACCENT,
            font=("Inter", 14, "bold")
        ).pack(side="left")

        self.status_label = tk.Label(
            top, text="", bg=BG_PANEL, fg=TEXT_DIM,
            font=("Inter", 11, "bold")
        )
        self.status_label.pack(side="right", padx=(8, 0))

        self.fps_label = tk.Label(
            top, text="", bg=BG_PANEL, fg=TEXT_DIM,
            font=("SF Mono", 10)
        )
        self.fps_label.pack(side="right", padx=(8, 0))

        # ── Connection info bar ───────────────────────────
        conn = tk.Frame(self.root, bg=BG_CARD, padx=12, pady=8)
        conn.pack(fill="x")

        # Mode badge
        self.mode_badge = tk.Label(
            conn, text="", bg=BG_CARD, fg=TEXT,
            font=("SF Mono", 10, "bold"), padx=8, pady=2
        )
        self.mode_badge.pack(side="left")

        # Device info text
        self.device_label = tk.Label(
            conn, text="", bg=BG_CARD, fg=TEXT_DIM,
            font=("SF Mono", 10)
        )
        self.device_label.pack(side="left", padx=(8, 0))

        # Action button
        self.action_btn = tk.Button(
            conn, text="", bg=ACCENT, fg="#fff",
            font=("Inter", 10, "bold"), relief="flat", padx=12, pady=2,
            command=self._on_action_btn, activebackground=ACCENT_GLOW,
            cursor="hand2"
        )
        self.action_btn.pack(side="right")

        # HID availability note
        if not self._hid_available:
            hid_note = tk.Label(
                conn, text="(HID mode unavailable — pygame not found)",
                bg=BG_CARD, fg=TEXT_DIM, font=("Inter", 9)
            )
            hid_note.pack(side="right", padx=(0, 8))

        # ── Manual connect row (visible when paused) ──────
        self.manual_frame = tk.Frame(self.root, bg=BG, padx=12, pady=4)

        tk.Label(
            self.manual_frame, text="Manual port:", bg=BG, fg=TEXT_DIM,
            font=("Inter", 10)
        ).pack(side="left")

        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            self.manual_frame, textvariable=self.port_var, width=32,
            font=("SF Mono", 10)
        )
        self.port_combo.pack(side="left", padx=(4, 4))

        self.manual_connect_btn = tk.Button(
            self.manual_frame, text="Connect", bg=ACCENT, fg="#fff",
            font=("Inter", 10, "bold"), relief="flat", padx=12, pady=2,
            command=self._manual_connect, activebackground=ACCENT_GLOW,
            cursor="hand2"
        )
        self.manual_connect_btn.pack(side="left")

        refresh_btn = tk.Button(
            self.manual_frame, text="⟳", bg=BG_CARD, fg=TEXT_DIM,
            font=("Inter", 12), relief="flat", padx=4, pady=0,
            command=self._refresh_ports, activebackground=BORDER,
            cursor="hand2"
        )
        refresh_btn.pack(side="left", padx=(4, 0))

        # ── Main content ─────────────────────────────────
        content = tk.Frame(self.root, bg=BG, padx=12, pady=4)
        content.pack(fill="both", expand=True)

        # Left: Stick visualizers stacked
        stick_frame = tk.Frame(content, bg=BG)
        stick_frame.pack(side="left", fill="y", padx=(0, 8))

        tk.Label(
            stick_frame, text="GIMBALS", bg=BG, fg=TEXT_DIM,
            font=("Inter", 9, "bold")
        ).pack(pady=(0, 4))

        self.left_stick = StickCanvas(stick_frame, "LEFT")
        self.left_stick.pack(pady=(0, 8))

        self.right_stick = StickCanvas(stick_frame, "RIGHT")
        self.right_stick.pack(pady=(0, 4))

        # Right: Channel bars in scrollable area
        bars_outer = tk.Frame(content, bg=BG)
        bars_outer.pack(side="left", fill="both", expand=True)

        tk.Label(
            bars_outer, text="ALL CHANNELS", bg=BG, fg=TEXT_DIM,
            font=("Inter", 9, "bold")
        ).pack(anchor="w", pady=(0, 4))

        # Scrollable channel bars
        bars_canvas = tk.Canvas(bars_outer, bg=BG_CARD, highlightthickness=0)
        bars_scrollbar = ttk.Scrollbar(
            bars_outer, orient="vertical", command=bars_canvas.yview
        )
        self.bars_container = tk.Frame(bars_canvas, bg=BG_CARD)

        self.bars_container.bind(
            "<Configure>",
            lambda e: bars_canvas.configure(scrollregion=bars_canvas.bbox("all"))
        )
        bars_canvas.create_window((0, 0), window=self.bars_container, anchor="nw")
        bars_canvas.configure(yscrollcommand=bars_scrollbar.set)

        def _on_mousewheel(event):
            bars_canvas.yview_scroll(-1 * (event.delta // 120 or event.delta), "units")

        bars_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        bars_canvas.pack(side="left", fill="both", expand=True)
        bars_scrollbar.pack(side="right", fill="y")

        # Create channel bars
        self.channel_bars: list[ChannelBar] = []
        for i in range(CHANNEL_COUNT):
            bar = ChannelBar(self.bars_container, i + 1)
            bar.pack(fill="x", padx=4, pady=1)
            self.channel_bars.append(bar)

    # ── Auto-Detection ────────────────────────────────────
    def _try_auto_connect(self):
        """Scan for BumbleBee and connect if found."""
        # Try USB-VCP serial first (preferred — 24 channels)
        port = detect_serial_bumblebee()
        if port:
            reader = SerialReader()
            reader.start(port, DEFAULT_BAUDRATE)
            self.active_reader = reader
            self.active_mode = "serial"
            self.device_info = port
            self.state = self.STATE_CONNECTING
            return

        # Try USB HID joystick
        if self._hid_available:
            js_id = detect_hid_bumblebee()
            if js_id is not None:
                reader = HidReader()
                reader.start(js_id)
                if reader.connected:
                    self.active_reader = reader
                    self.active_mode = "hid"
                    self.device_info = reader.device_name
                    self.state = self.STATE_CONNECTED
                else:
                    reader.stop()

    def _handle_disconnect(self):
        """Handle device disconnection — clean up and resume scanning."""
        if self.active_reader:
            self.active_reader.stop()
        self.active_reader = None
        self.active_mode = None
        self.device_info = ""
        self.state = self.STATE_SCANNING
        self._scan_tick = 0

    # ── Button Handlers ───────────────────────────────────
    def _on_action_btn(self):
        """Handle the main action button (Disconnect / Reconnect)."""
        if self.state in (self.STATE_CONNECTED, self.STATE_CONNECTING):
            # Disconnect and pause auto-detect
            if self.active_reader:
                self.active_reader.stop()
            self.active_reader = None
            self.active_mode = None
            self.device_info = ""
            self.state = self.STATE_PAUSED
            self._refresh_ports()
            self.manual_frame.pack(fill="x", after=self.root.winfo_children()[1])
        elif self.state == self.STATE_PAUSED:
            # Resume auto-detection
            self.manual_frame.pack_forget()
            self.state = self.STATE_SCANNING
            self._scan_tick = 0

    def _manual_connect(self):
        """Manually connect to a serial port."""
        port = self.port_var.get().strip()
        if not port:
            return
        reader = SerialReader()
        reader.start(port, DEFAULT_BAUDRATE)
        self.active_reader = reader
        self.active_mode = "serial"
        self.device_info = port
        self.state = self.STATE_CONNECTING
        self.manual_frame.pack_forget()

    def _refresh_ports(self):
        """Refresh the manual port selector dropdown."""
        ports = serial.tools.list_ports.comports()
        values = [p.device for p in ports]
        self.port_combo["values"] = values

    # ── Update Loop ───────────────────────────────────────
    def _schedule_update(self):
        self._tick()
        self.root.after(UPDATE_MS, self._schedule_update)

    def _tick(self):
        """Main update tick — handles state transitions and GUI updates."""
        self._scan_tick += 1

        # ── State machine ─────────────────────────────────
        if self.state == self.STATE_SCANNING:
            if self._scan_tick % SCAN_INTERVAL_FRAMES == 1:
                self._try_auto_connect()

        elif self.state == self.STATE_CONNECTING:
            # Wait for serial reader to confirm connection
            if self.active_reader:
                if self.active_reader.connected:
                    self.state = self.STATE_CONNECTED
                elif not self.active_reader.running:
                    # Connection failed
                    self._handle_disconnect()

        elif self.state == self.STATE_CONNECTED:
            # Poll HID reader if active
            if isinstance(self.active_reader, HidReader):
                self.active_reader.poll()

            # Check for disconnect
            if self.active_reader and not self.active_reader.connected and not self.active_reader.running:
                self._handle_disconnect()

        # ── Update display ────────────────────────────────
        self._update_gui()

    def _update_gui(self):
        """Update all visual elements."""
        # ── Channel data ──────────────────────────────────
        if self.active_reader:
            channels = self.active_reader.get_channels()
        else:
            channels = [0] * CHANNEL_COUNT

        # Update stick visualizers
        # CH1=Ail, CH2=Ele, CH3=Thr, CH4=Rud (same order for both modes)
        if len(channels) >= 4:
            self.left_stick.update_position(
                channels[3] / 1024.0,  # CH4 Rudder → X
                channels[2] / 1024.0,  # CH3 Throttle → Y
            )
            self.right_stick.update_position(
                channels[0] / 1024.0,  # CH1 Aileron → X
                channels[1] / 1024.0,  # CH2 Elevator → Y
            )

        # Update channel bars
        for i, bar in enumerate(self.channel_bars):
            if i < len(channels):
                bar.update_value(channels[i])

        # ── Status bar ────────────────────────────────────
        if self.state == self.STATE_SCANNING:
            self._scan_dots = (self._scan_dots + 1) % 120
            dots = "." * ((self._scan_dots // 30) + 1)
            self.status_label.configure(text=f"● SCANNING{dots}", fg=YELLOW)
            self.fps_label.configure(text="")
            self.mode_badge.configure(
                text=" ⟳ AUTO ",
                fg=YELLOW, bg=BG_CARD
            )
            self.device_label.configure(text="Waiting for BumbleBee...")
            self.action_btn.pack_forget()

        elif self.state == self.STATE_CONNECTING:
            self.status_label.configure(text="● CONNECTING...", fg=YELLOW)
            self.fps_label.configure(text="")
            mode_text = " VCP " if self.active_mode == "serial" else " HID "
            mode_color = VCP_COLOR if self.active_mode == "serial" else HID_COLOR
            self.mode_badge.configure(text=mode_text, fg="#fff", bg=mode_color)
            self.device_label.configure(text=self.device_info)
            self.action_btn.configure(text="Cancel", bg=RED)
            self.action_btn.pack(side="right")

        elif self.state == self.STATE_CONNECTED:
            reader = self.active_reader
            self.status_label.configure(text="● CONNECTED", fg=GREEN)
            if reader:
                self.fps_label.configure(
                    text=f"{reader.fps:.0f} fps  |  #{reader.frame_count}"
                )
            mode_text = " USB-VCP " if self.active_mode == "serial" else " HID "
            mode_color = VCP_COLOR if self.active_mode == "serial" else HID_COLOR
            self.mode_badge.configure(text=mode_text, fg="#fff", bg=mode_color)
            self.device_label.configure(text=self.device_info)
            self.action_btn.configure(text="Disconnect", bg=RED)
            self.action_btn.pack(side="right")

        elif self.state == self.STATE_PAUSED:
            self.status_label.configure(text="● PAUSED", fg=TEXT_DIM)
            self.fps_label.configure(text="")
            self.mode_badge.configure(text=" ◎ ", fg=TEXT_DIM, bg=BG_CARD)
            self.device_label.configure(text="Auto-detection paused")
            self.action_btn.configure(text="Reconnect", bg=ACCENT)
            self.action_btn.pack(side="right")

    # ── Run ───────────────────────────────────────────────
    def run(self):
        try:
            self.root.mainloop()
        finally:
            if self.active_reader:
                self.active_reader.stop()


def main():
    app = JoystickMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
