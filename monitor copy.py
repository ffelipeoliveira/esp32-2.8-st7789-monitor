import sys
import time

import requests
import serial
import serial.tools.list_ports as list_ports

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
    """Auto-detect the ESP32's COM port. Falls back to an interactive pick list."""
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
    label = "possible ESP32 ports" if matches else "no known ESP32 signature found -- available ports"
    print(f"Multiple {label}:")
    for i, p in enumerate(candidates):
        print(f"  [{i}] {p.device} - {p.description}")
    choice = input("Select port number: ")
    return candidates[int(choice)].device


def resolve_port():
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        return sys.argv[idx + 1]
    if SERIAL_PORT != "AUTO":
        return SERIAL_PORT
    return find_esp32_port()


def main():
    if "--discover" in sys.argv:
        discover()
        return

    port = resolve_port()
    print(f"Opening serial port {port} @ {BAUD_RATE}...")
    ser = serial.Serial(port, BAUD_RATE, timeout=1)
    time.sleep(2)

    print("Streaming data. Ctrl+C to stop.")
    try:
        while True:
            try:
                sensors = get_sensors()
                vals = {k: parse_number(find_value(sensors, *v)) for k, v in SENSOR_NAMES.items()}

                line = (f"{vals['cpu_temp']:.1f},{vals['cpu_usage']:.1f},"
                        f"{vals['gpu_temp']:.1f},{vals['gpu_usage']:.1f},"
                        f"{vals['ram_usage']:.1f}\n")
                ser.write(line.encode("utf-8"))
                print(line.strip())

            except requests.exceptions.RequestException as e:
                print(f"LibreHardwareMonitor web server not reachable: {e}")

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
