import os
import json
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

VERBOSE = "--verbose" in sys.argv

# Candidatos por chave, testados na ordem até um retornar valor numérico.
#
# Formatos aceitos por candidato:
#   ("nome", "categoria")
#   ("nome", "categoria", ["token1", "token2"])             # exige TODOS no path
#   ("nome", "categoria", ["token1"], ["token2", "token3"]) # exige uns, exclui outros
#
# O matching é por substring (case-insensitive). A "categoria" é buscada no path
# inteiro, então "Temperature" casa com a pasta "Temperatures" do LHM.
SENSOR_NAMES = {
    "cpu_temp": [
        ("CPU Package",       "Temperature"),  # Intel 2ª..14ª geração
        ("CPU (Tctl/Tdie)",   "Temperature"),  # AMD Ryzen
        ("CPU CCD1 (Tdie)",   "Temperature"),  # AMD Ryzen (por CCD)
        ("CPU Die (average)", "Temperature"),  # AMD Ryzen mais novos
        ("Core Average",      "Temperature"),  # fallback genérico
    ],
    "cpu_usage": [
        ("CPU Total", "Load"),
    ],
    "gpu_temp": [
        ("GPU Core",       "Temperature", ["NVIDIA"]),  # dGPU NVIDIA
        ("GPU Core",       "Temperature", ["AMD"]),     # dGPU AMD
        ("GPU Hot Spot",   "Temperature", ["AMD"]),     # RDNA hotspot
        ("GPU Core",       "Temperature", ["Intel"]),   # iGPU Intel
        ("GPU Temperature","Temperature", ["Intel"]),
        ("GPU Core",       "Temperature"),              # fallback (1ª GPU)
    ],
    "gpu_usage": [
        ("GPU Core", "Load", ["NVIDIA"]),
        ("GPU Core", "Load", ["AMD"]),
        ("GPU D3D",  "Load", ["Intel"]),
        ("GPU Core", "Load"),                            # fallback
    ],
    "ram_usage": [
        ("Memory",          "Load"),
        ("Physical Memory", "Load"),
    ],
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


# ---------- LHM helpers ----------

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


def find_value(sensors, name_sub, category_sub, path_include=None, path_exclude=None):
    """Retorna o valor do primeiro sensor que bate em nome + categoria + filtros de path."""
    name_sub = name_sub.lower()
    category_sub = category_sub.lower()
    path_include = [t.lower() for t in (path_include or [])]
    path_exclude = [t.lower() for t in (path_exclude or [])]

    for s in sensors:
        path_str = " ".join(s["path"]).lower()
        if name_sub not in s["text"].lower():
            continue
        if category_sub not in path_str:
            continue
        if path_include and not all(t in path_str for t in path_include):
            continue
        if path_exclude and any(t in path_str for t in path_exclude):
            continue
        return s["value"]
    return None


# ---------- Valor numérico ----------

_NON_NUMERIC_TOKENS = {"-", "--", "n/a", "na", "n.a.", "null", "none", ""}


def is_numeric(raw):
    """True se a string bruta pode virar um float útil."""
    if raw is None:
        return False
    s = str(raw).strip()
    if s.lower() in _NON_NUMERIC_TOKENS:
        return False
    cleaned = s.replace(",", ".")
    num = "".join(c for c in cleaned if c.isdigit() or c in ".-")
    if not num or num in (".", "-", "-.", ".-"):
        return False
    try:
        float(num)
        return True
    except ValueError:
        return False


def parse_number(raw):
    """Converte string bruta em float. None/ inválido -> 0.0."""
    if raw is None:
        return 0.0
    raw = str(raw).replace(",", ".")
    num = "".join(c for c in raw if c.isdigit() or c in ".-")
    try:
        return float(num) if num else 0.0
    except ValueError:
        return 0.0


# ---------- Resolução de candidatos ----------

def normalize_sensor_config(cfg):
    """Aceita o formato antigo (tupla) e o novo (lista de tuplas)."""
    out = {}
    for k, v in cfg.items():
        if isinstance(v, tuple):
            out[k] = [v]
        else:
            out[k] = list(v)
    return out


def normalize_candidate(c):
    """Aceita tuplas de 2, 3 ou 4 elementos. Retorna (name, cat, include, exclude)."""
    c = tuple(c)
    if len(c) == 2:
        return (c[0], c[1], [], [])
    if len(c) == 3:
        return (c[0], c[1], list(c[2]), [])
    if len(c) == 4:
        return (c[0], c[1], list(c[2]), list(c[3]))
    raise ValueError(f"Candidato inválido: {c!r}")


def resolve_sensor(sensors, key, candidates, cache, verbose=False):
    """
    Tenta o candidato cacheado primeiro; se falhar OU retornar valor não-numérico,
    invalida o cache e testa todos em ordem.
    Só cacheia candidatos que retornaram valor numérico.

    Retorna (raw_value_ou_None, idx_usado_ou_-1).
    """
    cached = cache.get(key)

    if cached is not None and 0 <= cached < len(candidates):
        name_sub, cat_sub, inc, exc = normalize_candidate(candidates[cached])
        v = find_value(sensors, name_sub, cat_sub, inc, exc)
        if v is not None and is_numeric(v):
            return v, cached
        if verbose:
            print(f"[sensor] {key}: cache #{cached} parou de responder "
                  f"({name_sub!r}/{cat_sub!r}) - reprocessando")
        cache.pop(key, None)

    for i, c in enumerate(candidates):
        name_sub, cat_sub, inc, exc = normalize_candidate(c)
        v = find_value(sensors, name_sub, cat_sub, inc, exc)
        if v is not None and is_numeric(v):
            cache[key] = i
            if verbose:
                extra = f" inc={inc}" if inc else ""
                extra += f" exc={exc}" if exc else ""
                print(f"[sensor] {key} -> #{i} {name_sub!r}/{cat_sub!r}{extra} = {v}")
            return v, i

    return None, -1


# ---------- Serial ----------

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


def resolve_port(port_selection=None):
    if port_selection is not None:
        selected = port_selection["port"]
        if selected != "AUTO":
            return selected
        return find_esp32_port()
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        return sys.argv[idx + 1]
    if SERIAL_PORT != "AUTO":
        return SERIAL_PORT
    return find_esp32_port()


def port_settings_path():
    app_data = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(app_data, "ESP32 Monitor", "settings.json")


def load_port_selection():
    try:
        with open(port_settings_path(), encoding="utf-8") as settings_file:
            selection = json.load(settings_file).get("port")
        return selection if isinstance(selection, str) and selection else "AUTO"
    except (OSError, ValueError, AttributeError):
        return "AUTO"


def save_port_selection(port):
    settings_path = port_settings_path()
    try:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as settings_file:
            json.dump({"port": port}, settings_file)
    except OSError as e:
        print(f"Could not save COM port selection: {e}")


# ---------- Tray icon ----------

def should_use_tray():
    if "--no-tray" in sys.argv:
        return False
    if "--tray" in sys.argv:
        return True
    return bool(getattr(sys, "frozen", False))


def create_tray_icon(stop_event, port_selection, reconnect_event):
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
        reconnect_event.set()
        icon.stop()

    def select_port(port):
        def action(_icon, _item):
            port_selection["port"] = port
            save_port_selection(port)
            reconnect_event.set()
        return action

    def port_menu_items():
        items = [pystray.MenuItem(
            "Auto-detect",
            select_port("AUTO"),
            checked=lambda _item: port_selection["port"] == "AUTO",
            radio=True,
        )]
        ports = list(list_ports.comports())
        for port in ports:
            description = port.description or ""
            label = f"{port.device} - {description}" if description else port.device
            items.append(pystray.MenuItem(
                label,
                select_port(port.device),
                checked=lambda _item, device=port.device: port_selection["port"] == device,
                radio=True,
            ))
        if not ports:
            items.append(pystray.MenuItem("No serial ports found", None, enabled=False))
        return items

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
            pystray.MenuItem("Serial Port", pystray.Menu(lambda: port_menu_items())),
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


# ---------- Monitor loop ----------

def monitor_loop(port, stop_event, tray_icon=None, port_selection=None, reconnect_event=None):
    ser = None
    state = "warning"

    sensor_cfg = normalize_sensor_config(SENSOR_NAMES)
    resolved_cache = {}   # {"cpu_temp": 0, "gpu_temp": 0, ...}

    print("Streaming data. Ctrl+C to stop.")
    if VERBOSE:
        print(f"[cfg] {len(sensor_cfg)} chaves, "
              f"{sum(len(v) for v in sensor_cfg.values())} candidatos no total")
    if tray_icon is not None:
        set_tray_state(tray_icon, state)

    try:
        while not stop_event.is_set():
            if reconnect_event is not None and reconnect_event.is_set():
                reconnect_event.clear()
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None

            if ser is None or not ser.is_open:
                try:
                    port = resolve_port(port_selection)
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
                    if reconnect_event is not None:
                        reconnect_event.wait(5)
                    else:
                        stop_event.wait(5)
                    if stop_event.is_set():
                        break
                    continue

            try:
                sensors = get_sensors()

                vals = {}
                missing = []
                for key, candidates in sensor_cfg.items():
                    raw, idx = resolve_sensor(
                        sensors, key, candidates, resolved_cache, verbose=VERBOSE
                    )
                    if raw is None:
                        missing.append(key)
                    vals[key] = parse_number(raw)

                # Garante que todas as chaves existem no dict (mesmo que 0.0)
                for key in ("cpu_temp", "cpu_usage", "gpu_temp", "gpu_usage", "ram_usage"):
                    vals.setdefault(key, 0.0)

                line = (f"{vals['cpu_temp']:.1f},{vals['cpu_usage']:.1f},"
                        f"{vals['gpu_temp']:.1f},{vals['gpu_usage']:.1f},"
                        f"{vals['ram_usage']:.1f}\n")
                ser.write(line.encode("utf-8"))
                print(line.strip())

                # Estado do tray: erro se faltar sensor crítico, warning se algum faltar,
                # ok caso contrário.
                if missing:
                    state = "warning"
                    if VERBOSE:
                        print(f"[sensor] faltando: {missing}")
                else:
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

            if reconnect_event is not None:
                reconnect_event.wait(POLL_INTERVAL)
                if stop_event.is_set():
                    break
            elif stop_event.wait(POLL_INTERVAL):
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


# ---------- Entrypoint ----------

def main():
    if "--discover" in sys.argv:
        discover()
        return

    stop_event = Event()
    reconnect_event = Event()
    selected_port = load_port_selection()
    if "--port" in sys.argv:
        selected_port = sys.argv[sys.argv.index("--port") + 1]
    elif SERIAL_PORT != "AUTO":
        selected_port = SERIAL_PORT
    port_selection = {"port": selected_port}

    if should_use_tray():
        tray_icon = create_tray_icon(stop_event, port_selection, reconnect_event)
        if tray_icon is not None:
            worker = Thread(
                target=monitor_loop,
                args=(None, stop_event, tray_icon, port_selection, reconnect_event),
                daemon=True,
            )
            worker.start()
            tray_icon.run()
            stop_event.set()
            reconnect_event.set()
            return

    monitor_loop(None, stop_event, port_selection=port_selection)


if __name__ == "__main__":
    main()