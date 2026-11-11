#!/usr/bin/env python3
"""ReLive Flasher - Programador ESP32 para controles BT (PS1, GameCube, N64, SNES, PS2...)

Estructura (todo junto en una misma carpeta, la puedes mover a donde quieras):

    relive-flasher/
        relive_flasher.py
        ReLive-Flasher.desktop      <- doble clic para abrir
        assets/                     <- iconos
        firmware/
            PS1/        <- bootloader.bin, partition-table.bin, ota_data_initial.bin, app.bin
            GameCube/
            N64/ ...

Cada subcarpeta de firmware/ es una consola. Para actualizar, reemplaza los .bin.
Las direcciones se leen de la partition-table.bin de cada consola.

Funciona en Linux y Windows.
Requiere (si lo abres como .py): Python con tk y las librerías de requirements.txt
    pip install -r requirements.txt        (en Arch, tk:  sudo pacman -S tk)
O usa el ejecutable que genera  python build.py  (Linux: ./build.sh, Windows: build.bat):
ya trae todo dentro y no instala nada.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import font as tkfont

from serial.tools import list_ports
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

try:
    import pty                      # solo Linux/macOS; en Windows se usan tuberías normales
except ImportError:
    pty = None

IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
USE_PTY = pty is not None and not os.environ.get("RELIVE_NO_PTY")

APP_NAME = "ReLive Flasher"
APP_SUB = "Programador ESP32"

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
FW_ROOT = os.path.join(BASE_DIR, "firmware")
CFG_FILE = os.path.join(BASE_DIR, "config.json")
try:                                    # lo escribe build.py al compilar: p. ej. "v1.0.3"
    with open(os.path.join(BASE_DIR, "version.txt"), encoding="utf-8") as _f:
        APP_VERSION = _f.read().strip()
except OSError:
    APP_VERSION = ""
ICON_FILE = os.path.join(BASE_DIR, "assets", "icon.png")

# --- actualizaciones (solo se revisan los .bin de aplicación publicados en GitHub)
REPO = "TecnoBit-Videojuegos/ReLiveBT-Controller"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
ASSET_RE = re.compile(r"^ReLiveBT_(.+)\.bin$", re.I)   # ReLiveBT_<CONSOLA>.bin
# nombre en el .bin publicado -> nombre de la carpeta, cuando no son iguales
# (ReLiveBT_GC.bin se aplica a la carpeta "GameCube"; si tu carpeta se llama "GC" también funciona)
CONSOLE_ALIASES = {"gc": "gamecube"}
BACKUP_DIR = "_backup"
DL_TRIES = 4              # GitHub a veces responde 502/504 de forma pasajera: se reintenta solo

BAUDS = ["115200", "230400", "460800", "921600"]
KNOWN_VIDS = {0x1A86, 0x10C4, 0x0403}  # CH340/CH9102, CP210x, FTDI

# --- paleta
BG = "#0e1116"
CARD = "#161b22"
CARD2 = "#1f2631"
BORDER = "#2a313c"
TRACK = "#252b36"
TEXT = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#4f8cff"
GREEN = "#22c55e"
RED = "#ef4444"
AMBER = "#f59e0b"

ROLE_LABELS = {"bootloader": "Bootloader", "partition": "Tabla de particiones",
               "ota": "OTA data", "app": "Aplicación"}
ROLE_ORDER = ["bootloader", "partition", "ota", "app"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
if IS_WIN:
    SANS, MONO = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    SANS, MONO = "Helvetica Neue", "Menlo"
else:
    SANS, MONO = "sans", "monospace"


# ---------------------------------------------------------------- utilidades
def esptool_cmd():
    """Cómo se lanza esptool.
    - Ejecutable empaquetado: el propio programa con --run-esptool (esptool va dentro).
    - Con Python: el esptool de este mismo entorno (el de requirements.txt), no uno suelto del sistema."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-esptool"]
    if importlib.util.find_spec("esptool"):
        return [sys.executable, "-m", "esptool"]
    for name in ("esptool", "esptool.py"):
        p = shutil.which(name)
        if p:
            return [p]
    return [sys.executable, "-m", "esptool"]


def esptool_version():
    try:
        import esptool
        return str(esptool.__version__)
    except Exception:  # noqa: BLE001
        return None


def run_embedded_esptool(args):
    """Modo interno del ejecutable: corre esptool con esos argumentos y termina."""
    import esptool
    # el .exe de Windows no tiene consola: asegurar que la salida y la entrada existan y sean UTF-8
    for name, fd, mode in (("stdout", 1, "w"), ("stderr", 2, "w")):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(fd, mode, encoding="utf-8", errors="replace", closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w"))
        else:
            try:
                getattr(sys, name).reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    if sys.stdin is None:
        sys.stdin = open(os.devnull)
    sys.argv = ["esptool"] + list(args)
    entry = getattr(esptool, "_main", None) or esptool.main
    try:
        entry()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001  (p. ej. OSError por tubería cerrada)
        try:
            import traceback
            traceback.print_exc()
        except Exception:  # noqa: BLE001
            pass
        os._exit(1)                      # sin ventana de «error no controlado» en el .exe
    sys.exit(0)


def find_port():
    for p in list_ports.comports():
        if p.vid in KNOWN_VIDS:
            return p.device
    return None


def load_cfg():
    cfg = {"console": None, "baud": "921600", "auto": False, "counts": {}, "win_off": None}
    try:
        with open(CFG_FILE) as f:
            saved = json.load(f)
        cfg.update({k: saved[k] for k in cfg if k in saved})
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


_CHILDREN = set()                  # procesos esptool en marcha
_CHILDREN_LOCK = threading.Lock()


def kill_children():
    """Detiene cualquier esptool que siga en marcha (al cerrar el programa). En Windows nadie más lo haría:
    el proceso quedaría huérfano y fallaría al intentar escribir en una tubería ya cerrada."""
    with _CHILDREN_LOCK:
        procs = list(_CHILDREN)
    for proc in procs:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def run_esptool(args, on_line=None, timeout=None):
    """Ejecuta esptool y entrega su salida línea a línea, en vivo.
    Linux: dentro de un pseudo-terminal. Windows: con tuberías (FORCE_COLOR=1 hace que esptool dibuje
    el avance igual que en una terminal)."""
    env = dict(os.environ, NO_COLOR="1", COLUMNS="110", PYTHONUNBUFFERED="1")
    cmd = esptool_cmd() + args
    if USE_PTY:
        master, slave = pty.openpty()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=slave,
                                stderr=slave, close_fds=True, env=env)
        os.close(slave)
        read = lambda: os.read(master, 4096)      # noqa: E731
        close = lambda: os.close(master)          # noqa: E731
    else:
        env.update(FORCE_COLOR="1", TTY_COMPATIBLE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, bufsize=0,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # sin ventana negra
        read = lambda: proc.stdout.read(4096)     # noqa: E731
        close = proc.stdout.close
    with _CHILDREN_LOCK:
        _CHILDREN.add(proc)
    killer = None
    if timeout:
        killer = threading.Timer(timeout, proc.kill)
        killer.start()
    buf = ""
    try:
        while True:
            try:
                data = read()
            except OSError:
                break
            if not data:
                break
            for ch in ANSI.sub("", data.decode(errors="replace")):
                if ch in "\r\n":
                    if buf.strip() and on_line:
                        on_line(buf)
                    buf = ""
                else:
                    buf += ch
    finally:
        try:
            close()
        except OSError:
            pass
    proc.wait()
    with _CHILDREN_LOCK:
        _CHILDREN.discard(proc)
    if killer:
        killer.cancel()
    return proc.returncode


# ---------------------------------------------------------------- firmware
def scan_consoles():
    if not os.path.isdir(FW_ROOT):
        return []
    return [d for d in sorted(os.listdir(FW_ROOT), key=str.lower)
            if not d.startswith(".") and os.path.isdir(os.path.join(FW_ROOT, d))]


def folder_signature():
    sig = []
    for c in scan_consoles():
        folder = os.path.join(FW_ROOT, c)
        files = []
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if os.path.isfile(p):
                st = os.stat(p)
                files.append((f, st.st_size, int(st.st_mtime)))
        sig.append((c, tuple(files)))
    return tuple(sig)


def parse_partition_table(path):
    data = open(path, "rb").read()
    parts = []
    for i in range(0, len(data) - 31, 32):
        e = data[i:i + 32]
        if e[:2] != b"\xaa\x50":
            break
        ptype, sub, off, size = struct.unpack("<BBII", e[2:12])
        parts.append((ptype, sub, off, size))
    return parts


def resolve(console):
    """Busca los .bin de la consola y calcula sus direcciones."""
    folder = os.path.join(FW_ROOT, console)
    groups = {r: [] for r in ROLE_ORDER}
    try:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if not (os.path.isfile(p) and f.lower().endswith(".bin")):
                continue
            n = f.lower()
            if "bootloader" in n:
                groups["bootloader"].append(p)
            elif "partition" in n:
                groups["partition"].append(p)
            elif "ota_data" in n:
                groups["ota"].append(p)
            else:
                groups["app"].append(p)
    except OSError:
        pass

    files, warn = {}, []
    for role, lst in groups.items():
        if lst:
            files[role] = max(lst, key=os.path.getmtime)  # el más reciente
            if len(lst) > 1:
                warn.append(f"Hay {len(lst)} archivos de {ROLE_LABELS[role]}; uso el más reciente.")

    addr = {"bootloader": 0x1000, "partition": 0x8000, "ota": 0xD000, "app": 0x10000}
    has_otadata = False
    if "partition" in files:
        try:
            parts = parse_partition_table(files["partition"])
            for ptype, sub, off, _size in parts:
                if ptype == 1 and sub == 0:
                    addr["ota"] = off
                    has_otadata = True
            factory = [p for p in parts if p[0] == 0 and p[1] == 0]
            ota0 = [p for p in parts if p[0] == 0 and p[1] == 0x10]
            if factory:
                addr["app"] = factory[0][2]
            elif ota0:
                addr["app"] = ota0[0][2]
        except Exception:
            warn.append("No pude leer la tabla de particiones; uso direcciones estándar.")
    if has_otadata and "ota" not in files:
        warn.append("La tabla usa OTA data pero no hay archivo ota_data_initial.")

    missing = [ROLE_LABELS[r] for r in ("bootloader", "partition", "app") if r not in files]
    return {"folder": folder, "files": files, "addr": addr, "warn": warn,
            "missing": missing, "ok": not missing}


# ---------------------------------------------------------------- acceso directo
def ensure_launchers():
    """Crea los lanzadores (menú de aplicaciones y carpeta del programa) con el icono. Solo Linux."""
    if not IS_LINUX:
        return
    try:
        if getattr(sys, "frozen", False):
            target_path = sys.executable
            exec_cmd = f'"{sys.executable}"'
        else:
            target_path = os.path.abspath(__file__)
            exec_cmd = f'python3 "{target_path}"'
        if any(c in target_path for c in '"$`\\'):
            return
        content = (
            "[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME}\nComment={APP_SUB}\n"
            f"Exec={exec_cmd}\nPath={BASE_DIR}\nIcon={ICON_FILE}\n"
            "Terminal=false\nCategories=Development;Electronics;\n"
        )
        launchers = [
            os.path.join(os.path.expanduser("~/.local/share/applications"), "relive-flasher.desktop"),
            os.path.join(BASE_DIR, "ReLive-Flasher.desktop"),
        ]
        for target in launchers:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            old = open(target).read() if os.path.exists(target) else ""
            if old != content:
                with open(target, "w") as f:
                    f.write(content)
                os.chmod(target, 0o755)
    except Exception:
        pass


# ---------------------------------------------------------------- actualizaciones (GitHub)
_HASH_CACHE = {}


def file_sha256(path):
    """sha256 de un archivo (con caché por fecha/tamaño para no recalcular)."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        if key not in _HASH_CACHE:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            _HASH_CACHE[key] = h.hexdigest()
        return _HASH_CACHE[key]
    except OSError:
        return None


def app_files(console):
    """Todos los .bin de la carpeta que cuentan como 'aplicación' (ni bootloader, ni partition, ni ota)."""
    folder = os.path.join(FW_ROOT, console)
    out = []
    try:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            n = f.lower()
            if os.path.isfile(p) and n.endswith(".bin") and not any(
                    k in n for k in ("bootloader", "partition", "ota_data")):
                out.append(p)
    except OSError:
        pass
    return out


def fetch_remote_apps():
    """Consulta GitHub. Devuelve {consola_en_minúsculas: info} con el ReLiveBT_<CONSOLA>.bin más reciente."""
    req = urllib.request.Request(RELEASES_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ReLive-Flasher"})
    with urllib.request.urlopen(req, timeout=8) as r:
        releases = json.loads(r.read().decode("utf-8"))
    found = {}
    for rel in releases:                      # vienen de la más nueva a la más vieja
        if rel.get("draft") or rel.get("prerelease"):
            continue
        for a in rel.get("assets", []):
            m = ASSET_RE.match(a.get("name", ""))
            if not m or not a.get("browser_download_url"):
                continue
            key = m.group(1).lower()
            if key in found:
                continue
            digest = a.get("digest") or ""
            found[key] = {
                "name": os.path.basename(a["name"]),
                "url": a["browser_download_url"],
                "size": a.get("size"),
                "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
                "tag": rel.get("tag_name", ""),
                "date": (a.get("updated_at") or "")[:10],
            }
    return found


def remote_for(console, remote):
    """Info publicada que corresponde a esta carpeta (por nombre o por alias)."""
    c = console.lower()
    for key, info in remote.items():
        if c in (key, CONSOLE_ALIASES.get(key)):
            return info
    return None


def update_available(console, remote):
    """Info del .bin publicado si es distinto al que usa hoy la consola, o si la carpeta no tiene
    ningún .bin de aplicación; None si está al día o si no hay nada publicado para esa consola."""
    info = remote_for(console, remote)
    local = app_files(console)
    if not info:
        return None
    if not local:
        return info
    newest = max(local, key=os.path.getmtime)    # el mismo que usa resolve()
    if info["sha256"]:
        return None if file_sha256(newest) == info["sha256"] else info
    if info["size"] and os.path.getsize(newest) == info["size"]:
        return None
    return info


def _retryable(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500 or e.code == 429
    return isinstance(e, (OSError, ValueError))      # cortes de red, timeouts, descarga incompleta


def download_app(console, info, progress, note=None):
    """Descarga y verifica el .bin. Solo si todo salió bien guarda copia del actual y lo reemplaza."""
    folder = os.path.join(FW_ROOT, console)
    target = os.path.join(folder, info["name"])
    tmp = target + ".part"
    req = urllib.request.Request(info["url"], headers={"User-Agent": "ReLive-Flasher"})
    for attempt in range(1, DL_TRIES + 1):
        h, done = hashlib.sha256(), 0
        try:
            with urllib.request.urlopen(req, timeout=20) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or info["size"] or 0)
                while True:
                    chunk = r.read(32 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if total:
                        progress(min(1.0, done / total))
            if info["size"] and done != info["size"]:
                raise ValueError("la descarga quedó incompleta")
            if info["sha256"] and h.hexdigest() != info["sha256"]:
                raise ValueError("el archivo descargado no coincide con el publicado")
            break
        except Exception as e:  # noqa: BLE001
            try:
                os.remove(tmp)
            except OSError:
                pass
            if attempt == DL_TRIES or not _retryable(e):
                raise
            if note:
                note(f"GitHub no respondió ({e}). Reintentando… ({attempt + 1}/{DL_TRIES})")
            time.sleep(2 * attempt)
    old = app_files(console)
    if old:                                      # copia de seguridad del/los actuales
        bdir = os.path.join(folder, BACKUP_DIR)
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for p in old:
            shutil.move(p, os.path.join(bdir, f"{stamp}_{os.path.basename(p)}"))
    os.replace(tmp, target)


# ---------------------------------------------------------------- widgets
def shade(color, amount):
    """Aclara (amount>0) u oscurece (amount<0) un color #rrggbb."""
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    if amount >= 0:
        r, g, b = (int(v + (255 - v) * amount) for v in (r, g, b))
    else:
        r, g, b = (int(v * (1 + amount)) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def rrect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


_FONT_CACHE = {}


def lh(font):
    """Altura real (px) de una línea de texto con esa fuente."""
    if font not in _FONT_CACHE:
        _FONT_CACHE[font] = tkfont.Font(font=font)
    return _FONT_CACHE[font].metrics("linespace")


class RButton(tk.Canvas):
    """Botón con esquinas redondeadas, hover y texto secundario opcional (sin que se pisen).
    El texto se encoge solo si no cabe. Puede mostrar una insignia de actualización (badge)."""
    GAP = 3
    BADGE_R = 11

    def __init__(self, master, text, command=None, bg=ACCENT, fg="#ffffff", font=(SANS, 14, "bold"),
                 height=52, radius=12, sub="", parent=BG, subfont=(SANS, 9), border=None, width=10):
        super().__init__(master, width=width, height=height, bg=parent, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.command, self.text, self.sub = command, text, sub
        self.bgc, self.fg, self.font, self.subfont = bg, fg, font, subfont
        self.radius, self.border, self.min_h = radius, border, height
        self.subfg = None
        self.enabled, self.hover = True, False
        self.badge, self.badge_cmd, self.badge_tip = False, None, ""
        self._tip = None
        self._fit()
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", self._leave)
        self.bind("<Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Destroy>", lambda e: self._hide_tip())

    def _block(self):
        h = lh(self.font)
        if self.sub:
            h += self.GAP + lh(self.subfont)
        return h

    def _fit(self):
        self.config(height=max(self.min_h, self._block() + 24))

    def look(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.config(cursor="hand2" if self.enabled else "arrow")
        self._fit()
        self.draw()

    def _hover(self, v):
        self.hover = v
        self.draw()

    def _leave(self, _e):
        self._hover(False)
        self._hide_tip()

    # ---- insignia de actualización
    def _over_badge(self, e):
        if not self.badge:
            return False
        cx, cy = self.winfo_width() - 18, 18
        return (e.x - cx) ** 2 + (e.y - cy) ** 2 <= (self.BADGE_R + 3) ** 2

    def _motion(self, e):
        if self._over_badge(e):
            self._show_tip()
        else:
            self._hide_tip()

    def _show_tip(self):
        if self._tip is not None or not self.badge_tip:
            return
        try:
            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tk.Label(tw, text=self.badge_tip, font=(SANS, 10), fg=TEXT, bg=CARD2, padx=10, pady=6,
                     highlightbackground=AMBER, highlightthickness=1).pack()
            tw.update_idletasks()
            x = self.winfo_rootx() + 4
            x = max(4, min(x, self.winfo_screenwidth() - tw.winfo_reqwidth() - 8))
            tw.wm_geometry("+%d+%d" % (x, self.winfo_rooty() + self.winfo_height() + 6))
            self._tip = tw
        except tk.TclError:
            self._tip = None

    def _hide_tip(self):
        tw, self._tip = self._tip, None
        if tw is not None:
            try:
                tw.destroy()
            except tk.TclError:
                pass

    def _click(self, e):
        if not (0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()):
            return
        if self._over_badge(e) and self.badge_cmd:
            self._hide_tip()
            self.badge_cmd()
        elif self.enabled and self.command:
            self.command()

    def _fit_font(self, text, font, max_w):
        """Devuelve la fuente más grande (hasta la original) en la que el texto cabe en max_w px."""
        fam, size, style = font[0], font[1], font[2:]
        while size > 7:
            f = (fam, size, *style)
            if tkfont.Font(font=f).measure(text) <= max_w:
                return f
            size -= 1
        return (fam, 7, *style)

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        color = shade(self.bgc, 0.12) if (self.hover and self.enabled) else self.bgc
        if self.border:
            rrect(self, 1, 1, w - 1, h - 1, self.radius, fill=color, outline=self.border, width=2)
        else:
            rrect(self, 0, 0, w, h, self.radius, fill=color)
        top = (h - self._block()) / 2
        main_h = lh(self.font)
        pad = 56 if self.badge else 20   # margen a los lados (más si hay insignia)
        main_font = self._fit_font(self.text, self.font, w - pad)
        self.create_text(w / 2, top + main_h / 2, text=self.text, fill=self.fg, font=main_font)
        if self.sub:
            sub_font = self._fit_font(self.sub, self.subfont, w - pad)
            self.create_text(w / 2, top + main_h + self.GAP + lh(self.subfont) / 2, text=self.sub,
                             fill=self.subfg or self.fg, font=sub_font)
        if self.badge:
            cx, cy, r = w - 18, 18, self.BADGE_R
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=AMBER, outline=BG, width=2)
            self.create_polygon(cx, cy - 6, cx - 6, cy, cx - 2.5, cy, cx - 2.5, cy + 6,
                                cx + 2.5, cy + 6, cx + 2.5, cy, cx + 6, cy, fill="#1a1200", outline="")


class Banner(tk.Canvas):
    """Cartel de estado. Mientras programa se va coloreando de izquierda a derecha."""
    R = 16
    MAIN = (SANS, 28, "bold")
    SUB = (SANS, 11)
    GAP = 6

    def __init__(self, master):
        super().__init__(master, width=10, height=10, bg=BG, highlightthickness=0, bd=0)
        self.config(height=max(96, lh(self.MAIN) + self.GAP + lh(self.SUB) + 36))
        self.track, self.fill = TRACK, None
        self.text, self.sub, self.fg = "", "", TEXT
        self.target = self.shown = 0.0
        self.animating = False
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, text, sub="", track=TRACK, fill=None, progress=0.0, fg=TEXT, reset=False):
        self.text, self.sub, self.track, self.fill, self.fg = text, sub, track, fill, fg
        self.target = progress
        if reset:
            self.shown = 0.0
        if fill is None:
            self.shown = self.target
        if self.shown != self.target and not self.animating:
            self.animating = True
            self.after(20, self._tick)
        self.draw()

    def _tick(self):
        diff = self.target - self.shown
        if abs(diff) < 0.002:
            self.shown, self.animating = self.target, False
        else:
            self.shown += diff * 0.35
            self.after(20, self._tick)
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        rrect(self, 0, 0, w, h, self.R, fill=self.track)
        if self.fill and self.shown > 0.001:
            fw = max(w * self.shown, 4)
            rrect(self, 0, 0, fw, h, self.R, fill=self.fill)
        mh = lh(self.MAIN)
        if self.sub:
            top = (h - (mh + self.GAP + lh(self.SUB))) / 2
            self.create_text(w / 2, top + mh / 2, text=self.text, fill=self.fg, font=self.MAIN)
            self.create_text(w / 2, top + mh + self.GAP + lh(self.SUB) / 2, text=self.sub, fill=self.fg,
                             font=self.SUB)
        else:
            self.create_text(w / 2, h / 2, text=self.text, fill=self.fg, font=self.MAIN)


class Switch(tk.Canvas):
    def __init__(self, master, command, parent=BG):
        super().__init__(master, width=50, height=28, bg=parent, highlightthickness=0, bd=0, cursor="hand2")
        self.on, self.command = False, command
        self.bind("<ButtonRelease-1>", self._click)
        self.draw()

    def set(self, value):
        self.on = value
        self.draw()

    def _click(self, _e):
        self.on = not self.on
        self.draw()
        self.command(self.on)

    def draw(self):
        self.delete("all")
        rrect(self, 1, 2, 49, 26, 12, fill=GREEN if self.on else "#3a4150")
        x = 36 if self.on else 14
        self.create_oval(x - 10, 4, x + 10, 24, fill="#ffffff", outline="")


class UpdateDialog(tk.Toplevel):
    """Ventana que ofrece descargar el nuevo .bin. No descarga nada hasta que se pulsa 'Descargar'."""

    def __init__(self, app, console, info):
        super().__init__(app.root)
        self.app, self.console, self.info = app, console, info
        self.working = False
        app.dialog_open = True
        self.title("Actualización disponible")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        pad = 24
        tk.Label(self, text=f"Actualización para {console}", font=(SANS, 16, "bold"), fg=TEXT,
                 bg=BG).pack(anchor="w", padx=pad, pady=(20, 2))
        has_local = bool(app_files(console))
        intro = ("Hay un firmware publicado en GitHub distinto al que tienes en esta carpeta." if has_local
                 else "Esta carpeta no tiene el .bin de aplicación y hay uno publicado en GitHub.")
        tk.Label(self, text=intro, font=(SANS, 11), fg=MUTED, bg=BG, wraplength=440,
                 justify="left").pack(anchor="w", padx=pad)
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=pad, pady=14)
        local = app_files(console)
        cur = os.path.basename(max(local, key=os.path.getmtime)) if local else "—"
        size = f"{info['size'] / 1024:.0f} KB" if info.get("size") else "—"
        rows = [("Versión publicada", f"{info['tag']}   ·   {info['date']}"),
                ("Archivo nuevo", f"{info['name']}  ({size})"),
                ("Tu archivo actual", cur)]
        for i, (k, v) in enumerate(rows):
            tk.Label(card, text=k, font=(SANS, 11), fg=MUTED, bg=CARD, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(14, 18), pady=5)
            tk.Label(card, text=v, font=(SANS, 11, "bold"), fg=TEXT, bg=CARD, anchor="w").grid(
                row=i, column=1, sticky="w", padx=(0, 14), pady=5)
        note = (f"Tu archivo actual se guarda en la carpeta {BACKUP_DIR} de la consola. " if has_local
                else "Solo se descarga el .bin de aplicación; bootloader y tabla de particiones deben estar en la carpeta. ")
        tk.Label(self, text=note + "No se descarga nada hasta que pulses Descargar.",
                 font=(SANS, 10), fg=MUTED, bg=BG, wraplength=440, justify="left").pack(anchor="w", padx=pad)
        self.bar = tk.Canvas(self, height=6, bg=TRACK, highlightthickness=0, bd=0)
        self.status = tk.Label(self, text="", font=(SANS, 10), fg=MUTED, bg=BG, wraplength=440,
                               justify="left")
        self.btns = tk.Frame(self, bg=BG)
        self.btns.pack(fill="x", padx=pad, pady=(14, 20))
        self.go = RButton(self.btns, "Descargar", self.start, bg=ACCENT, font=(SANS, 11, "bold"),
                          height=40, radius=10, parent=BG, width=130)
        self.go.pack(side="right")
        self.later = RButton(self.btns, "Ahora no", self.close, bg=CARD2, fg=TEXT, font=(SANS, 11, "bold"),
                             height=40, radius=10, parent=BG, width=110)
        self.later.pack(side="right", padx=(0, 8))

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        r = app.root
        x = r.winfo_rootx() + max(0, (r.winfo_width() - w) // 2)
        y = r.winfo_rooty() + max(20, (r.winfo_height() - h) // 3)
        self.geometry(f"+{x}+{y}")
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass

    def close(self):
        if self.working:
            return
        self.app.dialog_open = False
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def start(self):
        if self.working:
            return
        self.working = True
        self.app.busy = True                      # mientras descarga no se puede programar
        self.go.look(text="Descargando…", enabled=False, bgc=TRACK, fg=MUTED)
        self.later.look(enabled=False, fg=MUTED)
        self.bar.pack(fill="x", padx=24, pady=(12, 0), before=self.btns)
        self.status.pack(anchor="w", padx=24, pady=(6, 0), before=self.btns)
        self.status.config(text="Descargando… 0%", fg=MUTED)
        threading.Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            download_app(self.console, self.info, lambda p: self.app.ui(self.set_progress, p),
                         lambda m: self.app.ui(self.status.config, text=m, fg=AMBER))
        except Exception as e:  # noqa: BLE001
            self.app.ui(self.fail, e)
            return
        self.app.ui(self.done)

    def set_progress(self, p, color=ACCENT):
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, self.bar.winfo_width() * p, 6, fill=color, outline="")
        if color == ACCENT:
            self.status.config(text=f"Descargando… {int(p * 100)}%")

    def fail(self, err):
        self.app.busy = False
        self.working = False
        self.status.config(text=f"No se pudo descargar: {err}. Tu archivo actual no se modificó.", fg=RED)
        self.go.look(text="Reintentar", enabled=True, bgc=ACCENT, fg="#ffffff")
        self.later.look(enabled=True, fg=TEXT)

    def done(self):
        self.app.busy = False
        self.working = False
        self.set_progress(1.0, GREEN)
        self.status.config(text="✔  Actualizado. El nuevo firmware ya está listo para programar.", fg=GREEN)
        self.app.add_log(f"[actualización] {self.console}: {self.info['name']} ({self.info['tag']}) descargado")
        self.app.rebuild_all(reset_status=False)
        self.later.pack_forget()
        self.go.look(text="Cerrar", enabled=True, bgc=CARD2, fg=TEXT)
        self.go.command = self.close


# ---------------------------------------------------------------- aplicación
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_cfg()
        self.busy = False
        self.closing = False
        self.auto = False
        self.console = self.cfg["console"]
        self.auto_token = None
        self.sig = None
        self.log_visible = False
        self.remote = {}          # firmwares publicados en GitHub (se llena si hay internet)
        self.upd_state = "checking"   # "checking" | "ok" | ("error", motivo)
        self.dialog_open = False
        self.sized, self.base_h, self.log_extra, self.log_restore, self._anchor = False, 0, 0, None, None
        self._req = None                       # posición pedida al gestor de ventanas
        saved_off = self.cfg.get("win_off")    # desfase del gestor de ventanas (se aprende una vez y se guarda)
        self._off = tuple(saved_off) if saved_off else (0, 0)
        self.need_cal = not saved_off
        if self.need_cal:
            try:
                root.attributes("-alpha", 0.0)  # invisible mientras se calibra, para que no se note nada
            except tk.TclError:
                pass   # tamaño fijo de la ventana
        os.makedirs(FW_ROOT, exist_ok=True)

        root.title(f"{APP_NAME} {APP_VERSION} — {APP_SUB}" if APP_VERSION else f"{APP_NAME} — {APP_SUB}")
        root.configure(bg=BG)
        self.icon = None
        try:
            self.icon = tk.PhotoImage(file=ICON_FILE)
            root.iconphoto(True, self.icon)
        except Exception:
            pass
        root.option_add("*Menu.background", CARD2)
        root.option_add("*Menu.foreground", TEXT)
        root.option_add("*Menu.activeBackground", ACCENT)
        root.option_add("*Menu.activeForeground", "#ffffff")

        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind_all("<space>", self.on_key)
        root.bind_all("<Return>", self.on_key)

        self.rebuild_all(reset_status=True)
        self.fit_window()
        self.poll_folders()
        ensure_launchers()
        v = esptool_version()
        if v:
            self.add_log(f"[sistema] esptool {v}")
            if v.split(".")[0].isdigit() and int(v.split(".")[0]) < 5:
                self.add_log("[sistema] ⚠ Este programa necesita esptool 5 o superior. "
                             "Ejecuta:  pip install -r requirements.txt")
                self.show_log(True)
        self.check_updates()
        if self.cfg["auto"]:
            self.switch.set(True)
            self.toggle_auto(True)
        if self.need_cal:
            self.root.after(250, self.calibrate)

    # ---- construcción de la interfaz
    def build_ui(self):
        pad = 22
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=pad, pady=(18, 4))
        head.columnconfigure(0, weight=1, uniform="hd")
        head.columnconfigure(1, weight=0)
        head.columnconfigure(2, weight=1, uniform="hd")
        titles = tk.Frame(head, bg=BG)
        titles.grid(row=0, column=1)
        tk.Label(titles, text=APP_NAME, font=(SANS, 26, "bold"), fg=TEXT, bg=BG).pack()
        tk.Label(titles, text=APP_SUB.upper(), font=(SANS, 10, "bold"), fg=ACCENT, bg=BG).pack()

        tk.Label(self.root, text="CONSOLA", font=(SANS, 9, "bold"), fg=MUTED, bg=BG).pack(
            anchor="w", padx=pad + 2, pady=(14, 2))
        self.cons_frame = tk.Frame(self.root, bg=BG)
        self.cons_frame.pack(fill="x", padx=pad - 4)

        # tarjeta de archivos
        self.card = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.card.pack(fill="x", padx=pad, pady=(12, 6))
        ch = tk.Frame(self.card, bg=CARD)
        ch.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(ch, text="ARCHIVOS A GRABAR", font=(SANS, 10, "bold"), fg=MUTED, bg=CARD).pack(side="left")
        self.card_title = tk.Label(ch, text=" ", font=(SANS, 14, "bold"), fg=ACCENT, bg=CARD)
        self.card_title.pack(side="right")
        self.rows_frame = tk.Frame(self.card, bg=CARD)
        self.rows_frame.pack(fill="x", padx=16, pady=(0, 12))
        # altura de una fila = la de su celda más alta (se mide con etiquetas reales para que no varíe)
        cells = [("✔✘–", {"font": (SANS, 13, "bold")}, 10), ("Xg", {"font": (SANS, 12)}, 0),
                 ("0x10000", {"font": (MONO, 12, "bold"), "padx": 4, "pady": 2}, 0),
                 ("Xg", {"font": (SANS, 13, "bold")}, 0), ("999 KB", {"font": (SANS, 11)}, 0)]
        row_h = 0
        for text, kw, extra in cells:
            probe = tk.Label(self.rows_frame, text=text, **kw)
            row_h = max(row_h, probe.winfo_reqheight() + extra)
            probe.destroy()
        for i in range(len(ROLE_ORDER)):       # siempre 4 filas de alto, haya o no archivos
            self.rows_frame.rowconfigure(i, minsize=row_h)
        self.warn_lbl = tk.Label(self.card, text="", font=(SANS, 10), fg=AMBER, bg=CARD,
                                 wraplength=660, justify="left", anchor="nw", height=2)
        self.warn_lbl.pack(fill="x", padx=16, pady=(0, 10))      # 2 líneas reservadas siempre

        self.prog = RButton(self.root, "PROGRAMAR", self.start_manual, bg=ACCENT, font=(SANS, 24, "bold"),
                            height=78, radius=16, sub=" ", parent=BG, subfont=(SANS, 11, "bold"))   # sub=" " reserva la 2ª línea
        self.prog.pack(fill="x", padx=pad, pady=(8, 8))

        self.banner = Banner(self.root)
        self.banner.pack(fill="x", padx=pad, pady=(0, 6))

        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=pad, pady=(6, 2))

        # Lado izquierdo: Contador de PCBs
        self.count_lbl = tk.Label(foot, text=" ", font=(SANS, 11), fg=MUTED, bg=BG)
        self.count_lbl.pack(side="left", anchor="n")

        # Lado derecho: Controles y crédito agrupados en columna
        right_col = tk.Frame(foot, bg=BG)
        right_col.pack(side="right")

        # Fila superior de controles
        ctrls = tk.Frame(right_col, bg=BG)
        ctrls.pack(anchor="e")

        self.switch = Switch(ctrls, self.toggle_auto)
        self.switch.pack(side="right")
        tk.Label(ctrls, text="Modo automático", font=(SANS, 11), fg=TEXT, bg=BG).pack(side="right", padx=(0, 8))

        self.baud_var = tk.StringVar(value=self.cfg["baud"])
        om = tk.OptionMenu(ctrls, self.baud_var, *BAUDS, command=lambda v: self.save())
        om.config(bg=CARD2, fg=TEXT, activebackground=CARD2, activeforeground=TEXT, highlightthickness=0,
                  bd=0, font=(SANS, 10), relief="flat", width=7)
        om["menu"].config(bg=CARD2, fg=TEXT, activebackground=ACCENT, activeforeground="#ffffff", bd=0)
        om.pack(side="right", padx=(0, 28))
        tk.Label(ctrls, text="Velocidad", font=(SANS, 11), fg=MUTED, bg=BG).pack(side="right", padx=(0, 8))

        # Fila inferior: Texto discreto exactamente debajo del bloque derecho
        tk.Label(right_col, text="© 2026 TecnoBit_Videojuegos", font=(SANS, 9), fg="#484f58", bg=BG).pack(anchor="e", pady=(2, 0))

        logrow = tk.Frame(self.root, bg=BG)
        logrow.pack(fill="x", padx=pad, pady=(6, 14))
        self.log_toggle = tk.Label(logrow, text="▸ Mostrar registro", font=(SANS, 10), fg=MUTED,
                                   bg=BG, cursor="hand2")
        self.log_toggle.pack(side="left")
        self.upd_lbl = tk.Label(logrow, text="", font=(SANS, 10), fg=MUTED, bg=BG)
        self.upd_lbl.pack(side="right")
        self.log_toggle.bind("<Button-1>", lambda e: self.show_log(not self.log_visible))
        self.log_frame = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.log = tk.Text(self.log_frame, height=7, state="disabled", font=(MONO, 9), bg=CARD, fg=MUTED,
                           bd=0, highlightthickness=0, insertbackground=TEXT)
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    def show_log(self, visible):
        """Abre/cierra el registro: solo cambia la ALTURA (suma o resta lo que mide el registro).
        El ancho y la posición de la ventana no se tocan."""
        if visible == self.log_visible:
            return
        self.log_visible = visible
        if visible:
            self.log_frame.pack(fill="both", expand=True, padx=22, pady=(0, 14))
            self.log_toggle.config(text="▾ Ocultar registro")
            self.root.update_idletasks()
            self.log_extra = self.log_frame.winfo_reqheight() + 14
            self._resize_height(+self.log_extra)
        else:
            self.log_frame.pack_forget()
            self.log_toggle.config(text="▸ Mostrar registro")
            self._resize_height(-self.log_extra)

    def _geo_xy(self):
        """Posición (x, y) que Tk usa en geometry(); None si está anclada al borde derecho/inferior."""
        m = re.match(r"\d+x\d+([+-])(-?\d+)([+-])(-?\d+)", self.root.geometry())
        if not m or m.group(1) == "-" or m.group(3) == "-":
            return None
        return int(m.group(2)), int(m.group(4))

    def _resize_height(self, dy):
        """Cambia solo el alto del programa, sin mover la ventana."""
        root = self.root
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        nh = max(self.base_h, h + dy)
        ry = root.winfo_rooty()
        sh = root.winfo_screenheight()
        move = 0
        if dy > 0 and ry + nh > sh - 40:                         # no cabría en la pantalla: sube lo justo
            target = max(0, sh - 40 - nh)
            self.log_restore = (ry, target)
            move = target - ry
        elif dy < 0 and self.log_restore and abs(ry - self.log_restore[1]) <= 2:
            move = self.log_restore[0] - ry                      # nadie la movió: vuelve donde estaba
            self.log_restore = None
        elif dy < 0:
            self.log_restore = None
        self._apply_size(w, nh, move)

    def _apply_size(self, w, nh, move=0):
        """Aplica un tamaño nuevo manteniendo la ventana en su sitio.
        Algunos gestores de ventanas (p. ej. KDE) suman la barra de título a la posición que Tk les envía y
        la ventana se iba corriendo en cada cambio. Se mide dónde está de verdad (winfo_rootx/rooty), se
        corrige lo que se haya movido y se APRENDE ese desfase para compensarlo desde el siguiente cambio."""
        root = self.root
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        xy = self._geo_xy()
        shift = move
        if xy:
            gy = max(0, xy[1] + move)                            # la barra de título nunca sale por arriba
            shift = gy - xy[1]
            self._req = (xy[0] + self._off[0], gy + self._off[1])
            root.geometry(f"{w}x{nh}+{self._req[0]}+{self._req[1]}")
        else:
            self._req = None
            root.geometry(f"{w}x{nh}")
        self._anchor = (rx, ry + shift)
        for ms in (60, 200, 450, 800):                           # el gestor responde con retraso
            root.after(ms, self._hold_position)
        root.after(1000, lambda: setattr(self, "_anchor", None))

    def _hold_position(self):
        a = self._anchor
        if not a or self.closing or not self._req:
            return
        self.root.update_idletasks()
        ex, ey = self.root.winfo_rootx() - a[0], self.root.winfo_rooty() - a[1]
        if ex or ey:                                             # se movió: pedir la posición corregida
            self._req = (self._req[0] - ex, self._req[1] - ey)
            self._off = (self._off[0] - ex, self._off[1] - ey)   # y recordar el desfase para la próxima
            self.root.geometry(f"+{self._req[0]}+{self._req[1]}")
            self.cfg["win_off"] = list(self._off)
            save_cfg(self.cfg)

    def _settle(self, maxsec=0.9):
        """Deja pasar el tiempo que el gestor de ventanas tarda en responder, corrigiendo lo que se mueva."""
        root, t0, last, stable = self.root, time.time(), None, 0
        while time.time() - t0 < maxsec:
            root.update()
            time.sleep(0.02)
            self._hold_position()
            cur = (root.winfo_rootx(), root.winfo_rooty())
            stable = stable + 1 if cur == last else 0
            last = cur
            if stable >= 10 and time.time() - t0 > 0.2:
                break

    def calibrate(self):
        """Primer arranque en este equipo: hace dos cambios de tamaño mínimos con la ventana transparente,
        mide cuánto la mueve el gestor de ventanas y guarda ese desfase. Así, al abrir el registro por
        primera vez, no hay ningún movimiento."""
        root = self.root
        try:
            root.update()
            w, h = root.winfo_width(), root.winfo_height()
            for nh in (h + 2, h):
                self._apply_size(w, nh)
                self._settle()
            self.cfg["win_off"] = list(self._off)
            save_cfg(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._anchor = None                 # ya no se vigila: la ventana es del usuario
            try:
                root.attributes("-alpha", 1.0)
            except tk.TclError:
                pass

    def fit_window(self):
        """Tamaño inicial: se calcula UNA vez (con todo el espacio reservado) y la ventana se centra.
        Después ya no cambia al elegir consolas ni por el contenido."""
        if self.sized:
            return
        self.root.update_idletasks()
        sc = self.root.winfo_fpixels("1i") / 96.0                # 1.0 a 100 %, 1.5 a 150 %
        sc = 1.0 if sc < 1.2 else round(sc * 4) / 4              # solo se escala en pantallas de alta resolución
        w = min(int(1000 * sc), max(int(760 * sc), self.root.winfo_reqwidth()))
        h = self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
        self.base_h = h
        self.root.minsize(w, h)
        self.root.geometry(f"{w}x{h}+{max(0, x + self._off[0])}+{max(0, y + self._off[1])}")
        self.sized = True

    def ensure_fits(self):
        """Red de seguridad: solo crece (en alto, sin mover la ventana) si el contenido de verdad no cabe,
        por ejemplo si aparece una fila nueva de consolas."""
        if not self.sized:
            return
        self.root.update_idletasks()
        need, cur = self.root.winfo_reqheight(), self.root.winfo_height()
        if need > cur:
            self.base_h += need - cur
            self.root.minsize(self.root.winfo_width(), self.base_h)
            self._apply_size(self.root.winfo_width(), need)

    # ---- helpers UI
    def ui(self, fn, *a, **kw):
        if not self.closing:
            self.root.after(0, lambda: fn(*a, **kw))

    def add_log(self, line):
        self.log.config(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def save(self):
        self.cfg["console"] = self.console
        self.cfg["baud"] = self.baud_var.get()
        self.cfg["auto"] = self.auto
        save_cfg(self.cfg)

    def show_idle(self):
        if self.auto:
            self.banner.set("ESPERANDO PCB", track=TRACK)
        elif not self.console:
            self.banner.set("ELIGE UNA CONSOLA", track=TRACK, fg=MUTED)
        else:
            self.banner.set("LISTO", track=TRACK)

    # ---- consolas y archivos
    def rebuild_all(self, reset_status=True):
        self.sig = folder_signature()
        for w in self.cons_frame.winfo_children():
            w.destroy()
        consoles = scan_consoles()
        if self.console not in consoles:
            self.console = None
        self.cbtns = {}
        ncols = 5
        for c in range(ncols):
            self.cons_frame.columnconfigure(c, weight=1, uniform="c")
        for i, name in enumerate(consoles):
            ready = resolve(name)["ok"]
            b = RButton(self.cons_frame, name, lambda n=name: self.select(n), font=(SANS, 15, "bold"),
                        height=62, radius=12, sub="" if ready else "vacío", parent=BG)
            b.grid(row=i // ncols, column=i % ncols, sticky="we", padx=4, pady=4)
            self.cbtns[name] = (b, ready)
        if not consoles:
            tk.Label(self.cons_frame, text="No hay consolas. Crea carpetas dentro de 'firmware'.",
                     fg=MUTED, bg=BG).grid(row=0, column=0)
        self.refresh_view(reset_status)

    # ---- actualizaciones
    def check_updates(self):
        """Consulta GitHub UNA sola vez, al abrir el programa, en segundo plano.
        Sin internet se sigue trabajando offline; el resultado queda a la vista abajo a la derecha."""
        self.upd_state = "checking"
        self.refresh_upd_label()

        def work():
            try:
                remote = fetch_remote_apps()
            except urllib.error.HTTPError as e:
                why = ("límite de consultas de GitHub alcanzado" if e.code in (403, 429)
                       else f"GitHub respondió {e.code}")
                self.ui(self.update_failed, why)
            except Exception:  # noqa: BLE001
                self.ui(self.update_failed, "sin conexión")
            else:
                self.ui(self.set_remote, remote)

        threading.Thread(target=work, daemon=True).start()

    def update_failed(self, why):
        self.upd_state = ("error", why)
        self.add_log(f"[actualización] no se pudo consultar GitHub: {why}")
        self.refresh_upd_label()

    def set_remote(self, remote):
        self.remote = remote
        self.upd_state = "ok"
        self.apply_badges()
        consoles = scan_consoles()
        self.add_log("[actualización] publicados en GitHub: " + (", ".join(sorted(remote)) or "ninguno"))
        for key in sorted(remote):
            if not any(c.lower() in (key, CONSOLE_ALIASES.get(key)) for c in consoles):
                self.add_log(f'[actualización] "{key}" no coincide con ninguna carpeta de firmware/')
        pending = [c for c in consoles if update_available(c, remote)]
        for c in pending:
            self.add_log(f"[actualización] {c}: " + (
                "distinto al publicado" if app_files(c) else "la carpeta no tiene .bin de aplicación"))
        self.refresh_upd_label()

    def refresh_upd_label(self):
        """Texto de abajo a la derecha: habla de la consola seleccionada (o resume todas si no hay ninguna)."""
        st, c = self.upd_state, self.console
        if st == "checking":
            text, color = "Buscando actualizaciones…", MUTED
        elif st != "ok":
            text, color = f"No se pudo buscar actualizaciones: {st[1]}", AMBER
        elif not self.remote:
            text, color = "No hay firmwares ReLiveBT_*.bin publicados", AMBER
        elif c:
            if update_available(c, self.remote):
                text = (f"⬆ Actualización disponible para {c}" if app_files(c)
                        else f"⬆ {c}: hay firmware publicado para descargar")
                color = AMBER
            elif remote_for(c, self.remote):
                text, color = f"✔ {c} al día", GREEN
            else:
                text, color = f"{c}: sin firmware publicado en GitHub", MUTED
        else:
            consoles = scan_consoles()
            pending = [n for n in consoles if update_available(n, self.remote)]
            if pending:
                text, color = "⬆ Actualización disponible: " + ", ".join(pending), AMBER
            elif any(remote_for(n, self.remote) for n in consoles):
                text, color = "✔ Firmware al día", GREEN
            else:
                text, color = "Lo publicado no coincide con ninguna de tus carpetas", AMBER
        self.upd_lbl.config(text=text, fg=color)

    def apply_badges(self):
        for name, (b, _ready) in self.cbtns.items():
            info = update_available(name, self.remote)
            b.look(badge=bool(info), badge_cmd=lambda n=name: self.show_update(n),
                   badge_tip="Nueva versión disponible · clic para ver")

    def show_update(self, console):
        if self.busy or self.dialog_open:
            return
        info = update_available(console, self.remote)
        if info:
            UpdateDialog(self, console, info)

    def select(self, name):
        if self.busy:
            return
        self.console = name
        self.save()
        self.refresh_view(True)

    def refresh_view(self, reset_status=True):
        for name, (b, ready) in self.cbtns.items():
            if name == self.console:
                b.look(bgc=ACCENT, fg="#ffffff", subfg="#dbe7ff", border=None)
            elif ready:
                b.look(bgc=CARD2, fg=TEXT, subfg=MUTED, border=None)
            else:
                b.look(bgc=CARD, fg="#5b6472", subfg="#5b6472", border=None)
        self.apply_badges()

        for w in self.rows_frame.winfo_children():
            w.destroy()
        warn = ""
        if self.console:
            r = resolve(self.console)
            self.card_title.config(text=self.console)
            self.rows_frame.columnconfigure(3, weight=1)
            for i, role in enumerate(ROLE_ORDER):
                f = r["files"].get(role)
                need = role != "ota" or "ota" in r["files"]
                mark, mcolor = ("✔", GREEN) if f else (("✘", RED) if role != "ota" else ("–", MUTED))
                tk.Label(self.rows_frame, text=mark, font=(SANS, 13, "bold"), fg=mcolor, bg=CARD,
                         width=2).grid(row=i, column=0, pady=5)
                tk.Label(self.rows_frame, text=ROLE_LABELS[role], font=(SANS, 12), fg=MUTED, bg=CARD,
                         width=19, anchor="w").grid(row=i, column=1, sticky="w")
                if f:
                    tk.Label(self.rows_frame, text=hex(r["addr"][role]), font=(MONO, 12, "bold"), fg=ACCENT,
                             bg="#1b2a47", width=9, padx=4, pady=2).grid(row=i, column=2, padx=10)
                    tk.Label(self.rows_frame, text=os.path.basename(f), font=(SANS, 13, "bold"), fg=TEXT,
                             bg=CARD, anchor="w").grid(row=i, column=3, sticky="w")
                    tk.Label(self.rows_frame, text=f"{os.path.getsize(f) / 1024:.0f} KB", font=(SANS, 11),
                             fg=MUTED, bg=CARD).grid(row=i, column=4, sticky="e", padx=(10, 0))
                else:
                    tk.Label(self.rows_frame, text="", bg=CARD, width=9).grid(row=i, column=2, padx=10)
                    tk.Label(self.rows_frame, text="no hay archivo" if role != "ota" else "sin archivo (opcional)",
                             font=(SANS, 12), fg=RED if role != "ota" else MUTED, bg=CARD,
                             anchor="w").grid(row=i, column=3, sticky="w")
            warn = " ".join(r["warn"])
            if not r["ok"]:
                warn = ("Falta: " + ", ".join(r["missing"]) + ". " + warn).strip()
            self.prog.look(text="PROGRAMAR", sub=self.console, subfg="#dbe7ff", subfont=(SANS, 11, "bold"))
            self.count_lbl.config(text=f"{self.console} · {self.cfg['counts'].get(self.console, 0)} PCBs programadas")
        else:
            self.card_title.config(text=" ")
            tk.Label(self.rows_frame, text="Elige una consola para ver sus archivos.", font=(SANS, 12),
                     fg=MUTED, bg=CARD).grid(row=0, column=0, pady=4)
            self.prog.look(text="PROGRAMAR", sub=" ")
            self.count_lbl.config(text=" ")
        self.warn_lbl.config(text=warn)
        if reset_status and not self.busy:
            self.show_idle()
        self.refresh_upd_label()
        self.root.after_idle(self.ensure_fits)

    def poll_folders(self):
        if self.closing:
            return
        try:
            if not self.busy and folder_signature() != self.sig:
                self.rebuild_all(reset_status=False)
        except OSError:
            pass
        self.root.after(2000, self.poll_folders)

    # ---- programar
    def on_key(self, event):
        if self.dialog_open:
            return
        w = self.root.focus_get()
        if w is not None and w.winfo_class() in ("Entry", "Text", "Menubutton"):
            return
        if not self.auto:
            self.start_manual()

    def start_manual(self):
        if not self.busy:
            threading.Thread(target=self.flash_once, daemon=True).start()

    def flash_once(self):
        if self.busy:
            return False
        self.busy = True
        try:
            console = self.console
            if not console:
                self.ui(self.banner.set, "ELIGE UNA CONSOLA", track=AMBER, fg="#1a1200")
                return False
            r = resolve(console)
            if not r["ok"]:
                self.ui(self.banner.set, "FALTAN ARCHIVOS", "En firmware/%s falta: %s" % (
                    console, ", ".join(r["missing"])), track=AMBER, fg="#1a1200")
                return False
            port = find_port()
            if not port:
                self.ui(self.banner.set, "SIN PROGRAMADOR", "No encuentro el programador USB. Revisa el cable.",
                        track=RED, fg="#ffffff")
                return False

            items = sorted(((r["addr"][role], f) for role, f in r["files"].items()), key=lambda x: x[0])
            sizes = [os.path.getsize(f) for _a, f in items]
            total = float(sum(sizes)) or 1.0
            started = {"v": False}
            self.ui(self.banner.set, "CONECTANDO...", track=TRACK, fill=ACCENT, progress=0.0, reset=True)

            def on_line(line):
                if "Writing at" in line:
                    ma = re.search(r"Writing at 0x([0-9a-fA-F]+)", line)
                    mp = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                    if ma and mp:
                        a, pct = int(ma.group(1), 16), min(float(mp.group(1)), 100.0)
                        idx = max((k for k, (ad, _f) in enumerate(items) if ad <= a), default=0)
                        frac = (sum(sizes[:idx]) + sizes[idx] * pct / 100.0) / total
                        started["v"] = True
                        self.ui(self.banner.set, "PROGRAMANDO  %d%%" % int(frac * 100), track=TRACK,
                                fill=ACCENT, progress=frac)
                    return
                self.ui(self.add_log, line)

            args = ["--chip", "esp32", "--port", port, "--baud", self.baud_var.get(), "write-flash"]
            for addr, f in items:
                args += [hex(addr), f]

            self.ui(self.add_log, f"—— {console} ——")
            rc = run_esptool(args, on_line, timeout=180)
            if rc == 0:
                self.cfg["counts"][console] = self.cfg["counts"].get(console, 0) + 1
                save_cfg(self.cfg)
                self.ui(self.banner.set, "✔  LISTA", track=GREEN, fg="#ffffff")
                n = self.cfg["counts"][console]
                self.ui(self.count_lbl.config, text=f"{console} · {n} PCBs programadas")
                return True
            self.ui(self.banner.set, "✘  ERROR", "No se pudo programar. Presiona mejor los pines y repite.",
                    track=RED, fg="#ffffff")
            self.ui(self.show_log, True)
            return False
        except Exception as e:  # noqa: BLE001
            self.ui(self.banner.set, "✘  ERROR", str(e)[:90], track=RED, fg="#ffffff")
            return False
        finally:
            self.busy = False

    # ---- modo automático
    def toggle_auto(self, on):
        self.auto = bool(on)
        self.save()
        if self.auto:
            self.prog.look(text="AUTOMÁTICO ACTIVO", sub=" ", bgc=TRACK, enabled=False, fg=MUTED)
            self.banner.set("ESPERANDO PCB", track=TRACK)
            self.auto_token = object()
            threading.Thread(target=self.auto_loop, args=(self.auto_token,), daemon=True).start()
        else:
            self.auto_token = None
            self.prog.look(bgc=ACCENT, enabled=True, fg="#ffffff")
            self.refresh_view(True)

    def probe(self, port):
        try:
            return run_esptool(["--chip", "esp32", "--port", port, "--connect-attempts", "1", "chip-id"],
                               None, timeout=10) == 0
        except Exception:  # noqa: BLE001
            return False

    def auto_loop(self, token):
        armed = True
        while not self.closing and self.auto_token is token:
            if self.busy:
                time.sleep(0.3)
                continue
            if not self.console or not resolve(self.console)["ok"]:
                self.ui(self.banner.set, "ELIGE UNA CONSOLA", track=AMBER, fg="#1a1200")
                time.sleep(1)
                continue
            port = find_port()
            if not port:
                self.ui(self.banner.set, "SIN PROGRAMADOR", "Conecta el programador por USB", track=RED,
                        fg="#ffffff")
                time.sleep(1)
                continue
            present = self.probe(port)
            if armed and present:
                ok = self.flash_once()
                armed = False  # espera a que retires la PCB
                if ok:
                    self.ui(self.banner.set, "✔  LISTA", "Retira la PCB y pon la siguiente",
                            track=GREEN, fg="#ffffff")
            elif not armed and not present:
                armed = True
                self.ui(self.banner.set, "ESPERANDO PCB", track=TRACK)
            elif armed and not present:
                self.ui(self.banner.set, "ESPERANDO PCB", track=TRACK)
            time.sleep(0.3)

    def on_close(self):
        self.closing = True
        kill_children()
        self.root.destroy()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-esptool":
        run_embedded_esptool(sys.argv[2:])
    if IS_WIN:                                   # texto nítido en pantallas de alta resolución
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass
    r = tk.Tk(className="ReLiveFlasher")
    App(r)
    r.mainloop()
