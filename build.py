#!/usr/bin/env python3
"""Genera el ejecutable de ReLive Flasher (Linux y Windows). Lleva dentro Python, tk, esptool y pyserial,
así que en otra PC funciona sin instalar nada y sin internet.

Uso:
    python build.py                  construye en dist/ReLive-Flasher/
    python build.py --package        además crea el archivo para publicar (dist/*.tar.gz o dist/*.zip)
    python build.py --version v1.0.3 escribe la versión en version.txt (se muestra en el título de la ventana)
    python build.py --no-venv        usa el Python actual en vez de crear .build-venv

Cada consola de consoles.txt recibe en firmware/<Consola>/ los tres archivos base de base-files/
(bootloader.bin, partition-table.bin, ota_data_initial.bin). El .bin de aplicación lo descarga el
propio programa desde GitHub, o se copia a mano.

Se puede ejecutar de nuevo cuando cambies el código: reemplaza el programa pero NO toca tu firmware/,
tus descargas ni config.json dentro de dist/ReLive-Flasher/.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = "ReLive-Flasher"
PYINSTALLER = "pyinstaller==6.22.3"
IS_WIN = os.name == "nt"
BASE_FILES = ("bootloader.bin", "partition-table.bin", "ota_data_initial.bin")
EXE = APP + (".exe" if IS_WIN else "")
OUT = os.path.join(ROOT, "dist", APP)


def step(n, text):
    print(f"{n}/5  {text}", flush=True)


def run(cmd, **kw):
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def check_tk():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("A Python le falta tk (solo se necesita para construir). Instálalo y vuelve a ejecutar:")
        print("  Arch/Manjaro:  sudo pacman -S tk")
        print("  Debian/Ubuntu: sudo apt install python3-tk")
        print("  Fedora:        sudo dnf install python3-tkinter")
        print("  Windows:       reinstala Python marcando la opción «tcl/tk and IDLE»")
        sys.exit(1)


def read_consoles():
    path = os.path.join(ROOT, "consoles.txt")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


def arg_value(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def make_ico(png, ico):
    """Convierte assets/icon.png al .ico que necesita el .exe de Windows."""
    from PIL import Image
    img = Image.open(png).convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    square.save(ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def main():
    use_venv = "--no-venv" not in sys.argv
    check_tk()
    py = sys.executable

    step(1, "Preparando entorno de construcción (necesita internet solo esta vez)…")
    if use_venv:
        venv = os.path.join(ROOT, ".build-venv")
        if not os.path.isdir(venv):
            run([sys.executable, "-m", "venv", venv])
        py = os.path.join(venv, "Scripts" if IS_WIN else "bin", "python" + (".exe" if IS_WIN else ""))
    packages = ["-r", "requirements.txt", PYINSTALLER] + (["pillow"] if IS_WIN else [])
    run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--quiet"] + packages)

    step(2, "Construyendo el ejecutable (puede tardar un par de minutos)…")
    work = os.path.join(ROOT, ".build-work")
    dist = os.path.join(ROOT, ".build-dist")
    shutil.rmtree(dist, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", APP,
           "--distpath", dist, "--workpath", work, "--specpath", work,
           "--collect-all", "esptool", "--collect-submodules", "serial"]
    if IS_WIN:
        cmd.append("--windowed")                      # sin ventana de consola
        png = os.path.join(ROOT, "assets", "icon.png")
        if os.path.isfile(png):
            ico = os.path.join(work, "icon.ico")
            run([sys.executable, "-m", "pip", "install", "--quiet", "pillow"]); make_ico(png, ico)
            cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "relive_flasher.py"))
    log = os.path.join(ROOT, ".build-log.txt")
    with open(log, "w", encoding="utf-8") as f:
        if subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode != 0:
            print("Falló la construcción. Mira .build-log.txt")
            sys.exit(1)

    step(3, f"Armando la carpeta final ({os.path.relpath(OUT, ROOT)})…")
    os.makedirs(OUT, exist_ok=True)
    shutil.rmtree(os.path.join(OUT, "_internal"), ignore_errors=True)
    shutil.copytree(os.path.join(dist, APP), OUT, dirs_exist_ok=True)
    if os.path.isdir(os.path.join(ROOT, "assets")):
        shutil.copytree(os.path.join(ROOT, "assets"), os.path.join(OUT, "assets"), dirs_exist_ok=True)
    if os.path.isdir(os.path.join(ROOT, "firmware")) and not os.path.isdir(os.path.join(OUT, "firmware")):
        shutil.copytree(os.path.join(ROOT, "firmware"), os.path.join(OUT, "firmware"))
    os.makedirs(os.path.join(OUT, "firmware"), exist_ok=True)
    base = os.path.join(ROOT, "base-files")
    missing = [f for f in BASE_FILES if not os.path.isfile(os.path.join(base, f))]
    consoles = read_consoles()
    if consoles and missing:
        msg = f"Faltan archivos en base-files/: {', '.join(missing)}"
        if os.environ.get("GITHUB_ACTIONS"):
            print(msg)
            sys.exit(1)                                  # en GitHub: no publicar un paquete incompleto
        print("AVISO:", msg)
    for console in consoles:
        folder = os.path.join(OUT, "firmware", console)
        os.makedirs(folder, exist_ok=True)
        for name in BASE_FILES:                          # solo se copian si faltan: no pisa lo tuyo
            src = os.path.join(base, name)
            if os.path.isfile(src) and not os.path.exists(os.path.join(folder, name)):
                shutil.copy2(src, folder)
    version = arg_value("--version")
    version_file = os.path.join(OUT, "version.txt")
    if version:
        with open(version_file, "w", encoding="utf-8") as f:
            f.write(version + "\n")
    elif os.path.exists(version_file):
        os.remove(version_file)                          # compilación local: sin versión
    for doc in ("README.md", "LICENSE"):
        if os.path.isfile(os.path.join(ROOT, doc)):
            shutil.copy2(os.path.join(ROOT, doc), OUT)

    step(4, "Comprobando que esptool funciona dentro del ejecutable…")
    res = subprocess.run([os.path.join(OUT, EXE), "--run-esptool", "version"],
                         capture_output=True, text=True, timeout=120)
    lines = [l for l in (res.stdout + res.stderr).splitlines() if l.strip()]
    if res.returncode != 0 or not lines:
        print("esptool no respondió dentro del ejecutable:", res.stdout, res.stderr)
        sys.exit(1)
    print("     esptool", lines[-1].strip())

    step(5, "Empaquetando…" if "--package" in sys.argv else "Listo (sin empaquetar)")
    if "--package" in sys.argv:
        tag = "windows-x64" if IS_WIN else "linux-x64"
        os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
        if IS_WIN:
            archive = os.path.join(ROOT, "dist", f"{APP}-{tag}.zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                for folder, _dirs, files in os.walk(OUT):
                    for name in files:
                        full = os.path.join(folder, name)
                        z.write(full, os.path.join(APP, os.path.relpath(full, OUT)))
        else:
            archive = os.path.join(ROOT, "dist", f"{APP}-{tag}.tar.gz")
            with tarfile.open(archive, "w:gz") as t:
                t.add(OUT, arcname=APP)                  # conserva el permiso de ejecución
        print("     ", os.path.relpath(archive, ROOT))

    print(f"\nListo ✔  Programa en:  {os.path.relpath(os.path.join(OUT, EXE), ROOT)}")


if __name__ == "__main__":
    main()
