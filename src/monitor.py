"""
Serial monitor for the R2D2 USB-VCP Lua script running on EdgeTX radios.

The Lua script sends lines in the format:
    $<ch1>,<ch2>,<ch3>,...,<ch24>,\r\n

Channel values are raw EdgeTX outputs in the range -1024 to +1024.
"""

import sys
import time
import argparse
import serial
import serial.tools.list_ports


DEFAULT_PORT = "/dev/cu.usbmodem00000000001B1"
DEFAULT_BAUDRATE = 115200


def find_serial_ports():
    """List available serial ports to help the user pick the right one."""
    ports = serial.tools.list_ports.comports()
    return [(p.device, p.description) for p in ports]


def parse_r2d2_line(line: str) -> list[int] | None:
    """
    Parse a single R2D2 USB-VCP line.

    Expected format:  $val1,val2,...,val24,
    The line starts with '$' and each value is comma-separated.
    There is a trailing comma before the line ending.

    Returns a list of integer channel values, or None if the line is invalid.
    """
    line = line.strip()
    if not line.startswith("$"):
        return None

    # Strip the '$' prefix
    payload = line[1:]

    # Split on commas, filter out empty strings (trailing comma produces one)
    parts = [p.strip() for p in payload.split(",") if p.strip()]

    if not parts:
        return None

    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


def format_channels_compact(channels: list[int]) -> str:
    """Format channels in a compact single-line display for live monitoring."""
    pairs = []
    for i, val in enumerate(channels, start=1):
        # Normalize to -1.0 .. +1.0 range (EdgeTX outputs -1024 to +1024)
        norm = max(-1.0, min(1.0, val / 1024.0))
        pairs.append(f"CH{i:02d}:{norm:+6.2f}")
    return " | ".join(pairs)


def format_channels_full(channels: list[int]) -> str:
    """Format all channels in a multi-line table."""
    lines = []
    lines.append("┌──────┬───────┬────────────┬────────────────────────────────┐")
    lines.append("│  CH  │  Raw  │ Normalized │             Bar                │")
    lines.append("├──────┼───────┼────────────┼────────────────────────────────┤")

    for i, val in enumerate(channels, start=1):
        norm = max(-1.0, min(1.0, val / 1024.0))

        # Build a simple bar chart: 30 chars wide, center is at 15
        bar_width = 30
        center = bar_width // 2
        bar = list("·" * bar_width)
        bar[center] = "│"

        pos = int((norm + 1.0) / 2.0 * (bar_width - 1))
        pos = max(0, min(bar_width - 1, pos))
        bar[pos] = "█"

        bar_str = "".join(bar)
        lines.append(f"│  {i:02d}  │{val:+6d} │   {norm:+5.2f}    │{bar_str}│")

    lines.append("└──────┴───────┴────────────┴────────────────────────────────┘")
    return "\n".join(lines)


def run_raw_dump(port: str, baudrate: int, timeout: float):
    """Just dump everything that comes in from serial — useful for debugging."""
    print(f"[RAW DUMP] Connecting to {port} @ {baudrate} baud...")
    try:
        with serial.Serial(port, baudrate=baudrate, timeout=timeout) as ser:
            ser.reset_input_buffer()
            print("[RAW DUMP] Connected. Printing raw bytes. Press Ctrl+C to stop.\n")
            while True:
                if ser.in_waiting > 0:
                    raw = ser.read(ser.in_waiting)
                    # Print both hex and ascii representation
                    hex_str = raw.hex(" ")
                    ascii_str = raw.decode("utf-8", errors="replace")
                    print(f"[{len(raw):3d} bytes] HEX: {hex_str}")
                    print(f"           ASCII: {ascii_str!r}")
                    print()
                time.sleep(0.01)
    except serial.SerialException as e:
        print(f"\n[ERROR] Could not open {port}: {e}")
    except KeyboardInterrupt:
        print("\n[STOPPED]")


def run_monitor(port: str, baudrate: int, timeout: float, full: bool):
    """Main monitoring loop that parses R2D2 lines and displays channel values."""
    print("=" * 60)
    print(f" R2D2 USB-VCP Joystick Monitor")
    print(f" Port:     {port}")
    print(f" Baudrate: {baudrate}")
    print(f" Display:  {'Full table' if full else 'Compact (first 8 channels)'}")
    print("=" * 60)
    print("Connecting... ", end="", flush=True)

    try:
        with serial.Serial(port, baudrate=baudrate, timeout=timeout) as ser:
            ser.reset_input_buffer()
            print("OK")
            print("Streaming live values. Press Ctrl+C to stop.\n")

            buffer = ""
            frame_count = 0
            last_status_time = time.time()

            while True:
                if ser.in_waiting > 0:
                    raw_bytes = ser.read(ser.in_waiting)
                    buffer += raw_bytes.decode("utf-8", errors="ignore")

                    if "\n" in buffer:
                        lines = buffer.split("\n")
                        # Keep the last (possibly incomplete) fragment
                        buffer = lines[-1]

                        # Process the most recent complete line
                        for line in reversed(lines[:-1]):
                            channels = parse_r2d2_line(line)
                            if channels is not None:
                                frame_count += 1

                                if full:
                                    # Clear screen and redraw table
                                    print("\033[2J\033[H", end="")
                                    print(f"R2D2 USB-VCP Monitor | Frame #{frame_count}")
                                    print(format_channels_full(channels))
                                else:
                                    output = format_channels_compact(channels)
                                    print(f"\r{output}  [#{frame_count}]", end="", flush=True)

                                break  # Only display the newest frame

                # Periodic status if we haven't received anything
                now = time.time()
                if now - last_status_time > 5.0 and frame_count == 0:
                    print(f"\r[Waiting for data... {int(now - last_status_time)}s elapsed. "
                          f"Is the R2D2 Lua script running on your radio?]     ",
                          end="", flush=True)
                    last_status_time = now

                time.sleep(0.005)  # ~200Hz poll rate

    except serial.SerialException as e:
        print(f"\n[ERROR] Could not interact with port {port}.")
        print(f"  Details: {e}")
        print(f"\nAvailable serial ports:")
        for dev, desc in find_serial_ports():
            print(f"  {dev}  —  {desc}")
    except KeyboardInterrupt:
        print(f"\n\n[STOPPED] Received {frame_count} frames total.")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor raw joystick output from an EdgeTX radio via R2D2 USB-VCP Lua script."
    )
    parser.add_argument(
        "-p", "--port",
        default=DEFAULT_PORT,
        help=f"Serial port (default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "-b", "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help=f"Baud rate (default: {DEFAULT_BAUDRATE})"
    )
    parser.add_argument(
        "-f", "--full",
        action="store_true",
        help="Show all channels in a full table (default: compact first-8 display)"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Raw dump mode: print all incoming bytes without parsing (for debugging)"
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=float,
        default=0.1,
        help="Serial read timeout in seconds (default: 0.1)"
    )

    args = parser.parse_args()

    if args.list_ports:
        print("Available serial ports:")
        ports = find_serial_ports()
        if not ports:
            print("  (none found)")
        for dev, desc in ports:
            print(f"  {dev}  —  {desc}")
        return

    if args.raw:
        run_raw_dump(args.port, args.baudrate, args.timeout)
    else:
        run_monitor(args.port, args.baudrate, args.timeout, args.full)


if __name__ == "__main__":
    main()
