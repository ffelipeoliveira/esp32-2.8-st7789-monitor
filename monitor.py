import os
import sys
import time
from threading import Event, Thread

import requests
import serial
import serial.tools.list_ports as list_ports

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency for tray icon
    pystray = None
    Image = None
    ImageDraw = None

# ---------------- CONFIG ----------------
SERIAL_PORT = "AUTO"        # "AUTO" to auto-detect, or a fixed port like "COM5"
BAUD_RATE = 115200          # must match Serial.begin() on the ESP32
LHM_URL = "http://localhost:8085/data.json"
POLL_INTERVAL = 1.0         # seconds between updates

# (text substring to match, category substring to match)
# Run with --discover to see real names/categories for your hardware and adjust.
# Intel example: "CPU Package" / AMD example: "CPU (Tctl/Tdie)" or "Core (Tctl/Tdie)"
SENSOR_NAMES = {
    "cpu_temp":  ("CPU Package", "Temperature"),
    "cpu_usage": ("CPU Total",   "Load"),
    "gpu_temp":  ("GPU Core",    "Temperature"),
    "gpu_usage": ("GPU Core",    "Load"),
    "ram_usage": ("Memory",      "Load"),
}

# Known USB vendor:product IDs for common ESP32 dev boards.
KNOWN_VID_PID = {
    (0x303A, 0x1001),  # Espressif native USB-CDC (ESP32-C3/S3 built-in USB)
    (0x303A, 0x0002),  # Espressif native USB-JTAG/Serial
    (0x1A86, 0x7523),  # CH340
    (0x1A86, 0x55D4),  # CH9102 (CH340 variant used on some ESP32-C3 boards)
    (0x10C4, 0xEA60),  # CP2102/CP2104
}
KNOWN_DESC_KEYWORDS = ["CP210", "CH340", "CH9102", "USB-SERIAL", "USB JTAG", "SILICON LABS"]
# -----------------------------------------


def flatten(node, path=None, out=None):
    """Recursively flatten the LHM JSON tree into a list of leaf sensors."""
    if out is None:
        out = []
    if path is None:
        path = []
    children = node.get("Children", [])
    if not children:
        if node.get("Value") not in (None, "", "-"):
            out.append({
                "path": path + [node.get("Text", "")],
                "text": node.get("Text", ""),
                "value": node.get("Value", ""),
            })
        return out
    for child in children:
        flatten(child, path + [node.get("Text", "")], out)
    return out


def get_sensors():
    r = requests.get(LHM_URL, timeout=3)
    r.raise_for_status()
    return flatten(r.json())


def discover():
    for s in get_sensors():
        print(f"{' > '.join(s['path'])}  =  {s['value']}")
    print("\nMatch the sensor 'Text' (and the category folder above it, e.g. "
          "'Temperatures'/'Load') into SENSOR_NAMES in this script.")


def find_value(sensors, name_sub, category_sub):
    name_sub, category_sub = name_sub.lower(), category_sub.lower()
    for s in sensors:
        path_str = " ".join(s["path"]).lower()
        if name_sub in s["text"].lower() and category_sub in path_str:
            return s["value"]
    return None


def parse_number(raw):
    if raw is None:
        return 0.0
    raw = str(raw).replace(",", ".")
    num = "".join(c for c in raw if c.isdigit() or c in ".-")
    try:
        return float(num) if num else 0.0
    except ValueError:
        return 0.0


def find_esp32_port():
    """Auto-detect the ESP32's COM port and fall back to the first available candidate."""
    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("No serial ports found. Is the ESP32 plugged in?")

    matches = []
    for p in ports:
        if (p.vid, p.pid) in KNOWN_VID_PID:
            matches.append(p)
            continue
        desc = f"{p.description or ''} {p.manufacturer or ''}".upper()
        if any(k in desc for k in KNOWN_DESC_KEYWORDS):
            matches.append(p)

    if len(matches) == 1:
        print(f"Auto-detected ESP32 on {matches[0].device} ({matches[0].description})")
        return matches[0].device

    candidates = matches if matches else ports
    if not candidates:
        raise RuntimeError("No usable serial ports were found for the ESP32.")

    first_port = candidates[0]
    print(f"Using first detected port: {first_port.device} ({first_port.description})")
    return first_port.device


def resolve_port():
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        return sys.argv[idx + 1]
    if SERIAL_PORT != "AUTO":
        return SERIAL_PORT
    return find_esp32_port()


def should_use_tray():
    if "--no-tray" in sys.argv:
        return False
    if "--tray" in sys.argv:
        return True
    return bool(getattr(sys, "frozen", False))


def create_tray_icon(stop_event):
    if pystray is None or Image is None:
        print("pystray/pillow not installed; install with: python -m pip install pystray pillow")
        return None

    def load_icon(path):
        try:
            if os.path.exists(path):
                return Image.open(path)
        except Exception:
            pass
        return None

    def build_image(color=(58, 170, 92, 255)):
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill=(28, 31, 36, 255))
        draw.rounded_rectangle((14, 14, 50, 50), radius=8, fill=(18, 22, 27, 255))
        draw.rectangle((20, 18, 44, 42), fill=color)
        draw.line((20, 46, 44, 46), fill=(255, 255, 255, 180), width=3)
        return image

    def quit_action(icon, _item):
        stop_event.set()
        icon.stop()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(base_dir, "icons", "ok.ico")
    tray_image = load_icon(icon_path) or build_image()

    icon = pystray.Icon(
        "esp32-monitor",
        tray_image,
        "ESP32 Monitor",
        menu=pystray.Menu(
            pystray.MenuItem("ESP32 Monitor", lambda: None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_action),
        ),
    )
    return icon


def set_tray_state(icon, state):
    if icon is None:
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    icon_file = {
        "ok": "ok.ico",
        "warning": "warning.ico",
        "error": "error.ico",
    }.get(state, "ok.ico")
    icon_path = os.path.join(base_dir, "icons", icon_file)

    try:
        icon.icon = Image.open(icon_path)
    except Exception:
        pass

    icon.title = f"ESP32 Monitor - {state.title()}"


def monitor_loop(port, stop_event, tray_icon=None):
    ser = None
    state = "warning"

    print("Streaming data. Ctrl+C to stop.")
    if tray_icon is not None:
        set_tray_state(tray_icon, state)

    try:
        while not stop_event.is_set():
            if ser is None or not ser.is_open:
                try:
                    port = resolve_port()
                    ser = serial.Serial(port, BAUD_RATE, timeout=1)
                    time.sleep(2)
                    print(f"Opened serial port {port} @ {BAUD_RATE}")
                    state = "warning"
                except Exception as e:
                    print(f"Waiting for ESP32 on {port or 'auto-detected port'}: {e}")
                    if ser is not None:
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = None
                    if tray_icon is not None:
                        set_tray_state(tray_icon, "error")
                    if stop_event.wait(5):
                        break
                    continue

            try:
                sensors = get_sensors()
                vals = {k: parse_number(find_value(sensors, *v)) for k, v in SENSOR_NAMES.items()}

                line = (f"{vals['cpu_temp']:.1f},{vals['cpu_usage']:.1f},"
                        f"{vals['gpu_temp']:.1f},{vals['gpu_usage']:.1f},"
                        f"{vals['ram_usage']:.1f}\n")
                ser.write(line.encode("utf-8"))
                print(line.strip())
                state = "ok"

            except serial.SerialException as e:
                print(f"Serial port error: {e}")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                state = "error"
            except requests.exceptions.RequestException as e:
                print(f"LibreHardwareMonitor web server not reachable: {e}")
                state = "warning"

            if tray_icon is not None:
                set_tray_state(tray_icon, state)

            if stop_event.wait(POLL_INTERVAL):
                break
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        print("Monitor stopped.")


def main():
    if "--discover" in sys.argv:
        discover()
        return

    stop_event = Event()

    if should_use_tray():
        tray_icon = create_tray_icon(stop_event)
        if tray_icon is not None:
            worker = Thread(target=monitor_loop, args=(None, stop_event, tray_icon), daemon=True)
            worker.start()
            tray_icon.run()
            stop_event.set()
            return

    monitor_loop(None, stop_event)


if __name__ == "__main__":
    main()
