"""
HID Joystick reader for EdgeTX radios in USB Joystick mode.

When the BumbleBee is connected in Joystick mode (not Serial/VCP mode),
it appears as a standard USB HID gamepad. This module reads axis and
button values using pygame's joystick subsystem.

Unlike the serial reader, this runs in the main thread — call poll()
from the tkinter update loop each frame.
"""

import time

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

CHANNEL_COUNT = 32
BUMBLEBEE_HID_KEYWORDS = ["bumblebee"]


def init_pygame_joystick():
    """
    Initialize pygame subsystems needed for HID joystick. Call once on main thread.

    Uses the SDL dummy video driver to avoid conflicting with tkinter's
    Cocoa event loop on macOS (both SDL and Tk want to own NSApplication).
    """
    if not HAS_PYGAME:
        return False
    try:
        import os
        # Dummy video driver prevents SDL from touching NSApplication (avoids tkinter crash)
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        # Allow joystick events even when the (dummy) SDL window isn't focused
        os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
        pygame.display.init()
        pygame.joystick.init()
        return True
    except pygame.error:
        return False


def detect_hid_bumblebee() -> int | None:
    """
    Scan for a BumbleBee in USB HID joystick mode.
    Returns the pygame joystick index, or None if not found.
    """
    if not HAS_PYGAME:
        return None
    try:
        # Re-init to pick up newly connected devices
        pygame.joystick.quit()
        pygame.joystick.init()
        for i in range(pygame.joystick.get_count()):
            js = pygame.joystick.Joystick(i)
            name = js.get_name().lower()
            if any(kw in name for kw in BUMBLEBEE_HID_KEYWORDS):
                return i
    except pygame.error:
        pass
    return None


class HidReader:
    """
    Reads joystick axes and buttons from a USB HID gamepad.

    Axes are mapped to channels 0..N as values in -1024..+1024.
    Buttons are mapped to channels N+1.. as -1024 (off) or +1024 (on).

    This reader runs in the main thread. Call poll() each frame from
    the tkinter update loop.
    """

    def __init__(self):
        self.channels: list[int] = [0] * CHANNEL_COUNT
        self.connected = False
        self.running = False
        self.frame_count = 0
        self.fps = 0.0
        self.error_msg = ""
        self.mode = "hid"
        self.device_name = ""
        self.num_axes = 0
        self.num_buttons = 0
        self._js = None
        self._instance_id: int | None = None
        self._fps_counter = 0
        self._fps_time = time.time()

    def start(self, joystick_id: int = 0):
        """Initialize and start reading from the given joystick index."""
        if not HAS_PYGAME:
            self.error_msg = "pygame not installed"
            return
        self.stop()
        try:
            self._js = pygame.joystick.Joystick(joystick_id)
            self._js.init()
            self._instance_id = self._js.get_instance_id()
            self.device_name = self._js.get_name()
            self.num_axes = self._js.get_numaxes()
            self.num_buttons = self._js.get_numbuttons()
            self.connected = True
            self.running = True
            self.frame_count = 0
            self.channels = [0] * CHANNEL_COUNT
            self._fps_counter = 0
            self._fps_time = time.time()
            self.error_msg = ""
        except pygame.error as e:
            self.error_msg = str(e)
            self.connected = False
            self.running = False

    def stop(self):
        """Stop reading and release the joystick."""
        if self._js:
            try:
                self._js.quit()
            except Exception:
                pass
            self._js = None
        self._instance_id = None
        self.connected = False
        self.running = False
        self.device_name = ""

    def poll(self):
        """
        Read current joystick state. Must be called from the main thread
        each frame (from the tkinter update loop).
        """
        if not self.connected or self._js is None:
            return

        # Process events to update joystick state and detect disconnects
        try:
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.JOYDEVICEREMOVED:
                    removed_id = getattr(event, "instance_id", None)
                    if removed_id is None or removed_id == self._instance_id:
                        self.connected = False
                        self.running = False
                        return
        except pygame.error:
            self.connected = False
            self.running = False
            return

        try:
            channels = [0] * CHANNEL_COUNT

            # Axes → first N channels (±1.0 scaled to ±1024)
            for i in range(min(self.num_axes, CHANNEL_COUNT)):
                channels[i] = int(self._js.get_axis(i) * 1024)

            # Buttons → next channels (-1024 off / +1024 on)
            btn_offset = self.num_axes
            for i in range(self.num_buttons):
                ch_idx = btn_offset + i
                if ch_idx < CHANNEL_COUNT:
                    channels[ch_idx] = 1024 if self._js.get_button(i) else -1024

            self.channels = channels
            self.frame_count += 1
            self._fps_counter += 1

            # FPS calculation
            now = time.time()
            if now - self._fps_time >= 1.0:
                self.fps = self._fps_counter / (now - self._fps_time)
                self._fps_counter = 0
                self._fps_time = now

        except pygame.error as e:
            self.error_msg = str(e)
            self.connected = False
            self.running = False

    def get_channels(self) -> list[int]:
        """Return a copy of the current channel values."""
        return list(self.channels)
