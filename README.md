# ReLive Flasher

[![Latest release](https://img.shields.io/github/v/release/TecnoBit-Videojuegos/ReLive-Flasher?label=download&color=4f8cff)](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/releases/latest)
[![Build](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/actions/workflows/build.yml/badge.svg)](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/actions/workflows/build.yml)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-lightgrey)

A small desktop tool to program the ESP32 boards of the **ReLiveBT** controllers, one console at a time,
with a single click. It is built for workshop use: pick a console, place the PCB on the programmer and
press a key. No Python, no command line and no drivers to configure beyond the USB adapter itself.

The firmware itself is published separately in
[TecnoBit-Videojuegos/ReLiveBT-Controller](https://github.com/TecnoBit-Videojuegos/ReLiveBT-Controller).
This repository only contains the programming tool.

> The program interface is in Spanish.

<table>
  <tr>
    <td align="center"><b>Linux</b><br><img src="docs/screenshots/main-linux.png" width="380" alt="ReLive Flasher on Linux"></td>
    <td align="center"><b>Windows</b><br><img src="docs/screenshots/main-windows.png" width="380" alt="ReLive Flasher on Windows"></td>
  </tr>
</table>

## Features

- **One-click flashing** of the bootloader, partition table, OTA data and application, at the addresses
  read from each console's `partition-table.bin` (defaults: `0x1000`, `0x8000`, `0xD000`, `0x10000`).
- **One folder per console.** Add a console by creating a folder; the program lists what it found and
  tells you exactly what is missing.
- **Manual mode** (any key or click programs the board) and **automatic mode** (detects a new PCB, programs
  it and waits until you remove it before the next one).
- **Programmer auto-detection** for the common USB-serial chips (CH340/CH9102, CP210x and FTDI).
- **Counter of programmed PCBs** per console.
- **Optional firmware updates.** Once at startup the program checks GitHub for newer firmware and marks
  the console button with a small indicator. Updating is always your choice. Everything keeps working
  offline with the files you already have.
- **Live log** with the full esptool output.
- Fixed-size dark window that scales with high-resolution displays.
- **Self-contained downloads:** Python and esptool are bundled inside.

## Download

Get the latest version from the **[Releases page](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/releases/latest)**.
There is one file per system:

| System | File | How to run |
| --- | --- | --- |
| Windows (64-bit) | `ReLive-Flasher-windows-x64.zip` | Extract it, then run `ReLive-Flasher.exe` |
| Linux (x86-64) | `ReLive-Flasher-linux-x64.tar.gz` | `tar xzf ReLive-Flasher-linux-x64.tar.gz`, then `./ReLive-Flasher/ReLive-Flasher` |

Extract the whole folder and keep it together: the program stores the console firmware, its settings and the
programmed-PCB counters next to the executable, so you can move the folder anywhere.

### Windows notes

- The executable is not code-signed, so Windows SmartScreen may show *"Windows protected your PC"* the first
  time. Click **More info → Run anyway**.
- Plug in the programmer and check that a new entry appears in **Device Manager → Ports (COM & LPT)**.
  Windows 10/11 usually installs the driver by itself. If it does not (typical for CH340 adapters), install
  the driver from the chip manufacturer: WCH for CH340/CH9102, Silicon Labs for CP210x, FTDI for FTDI chips.

### Linux notes

- The Linux build is compiled on Ubuntu 24.04 and needs **glibc 2.39 or newer** (Ubuntu 24.04+, Fedora 40+,
  Debian 13+, Arch-based distributions such as CachyOS or Manjaro).
- Your user needs permission to use serial ports. Add yourself to the right group, then log out and in again:
  - Arch / CachyOS / Manjaro: `sudo usermod -aG uucp $USER`
  - Debian / Ubuntu / Fedora: `sudo usermod -aG dialout $USER`
- On first run the program creates an application-menu launcher with its icon.

## Using it

1. Plug in the USB programmer. It is detected automatically.
2. Choose the console with the buttons at the top.
3. Check the **Archivos a grabar** card (files to flash). Every row needs a green check; the card also
   shows each file's address and size, and a yellow note lists what is missing.
4. Put the PCB on the programmer and press **PROGRAMAR**, or simply press any key.
   The big banner shows the progress and then **LISTO** (done) or the error.
5. For production runs, switch on **Modo automático**: it waits for a PCB, programs it, and waits for
   you to remove it before the next one.

**Velocidad** is the flashing speed (115200 to 921600, default 921600). Lower it if flashing is unstable
with long cables or poor contact.

<table>
  <tr>
    <td align="center"><b>Firmware update available</b><br><img src="docs/screenshots/update-dialog.png" width="380" alt="Firmware update dialog"></td>
    <td align="center"><b>Log panel</b><br><img src="docs/screenshots/log-panel.png" width="380" alt="Log panel"></td>
  </tr>
</table>

## Firmware files

Each console has its own folder inside `firmware/`:

```
ReLive-Flasher/
├── ReLive-Flasher(.exe)
├── firmware/
│   ├── GameCube/
│   │   ├── bootloader.bin
│   │   ├── partition-table.bin
│   │   ├── ota_data_initial.bin
│   │   └── ReLiveBT_GC.bin          <- application (downloaded by the program, or copied by hand)
│   ├── N64/
│   ├── PS1/
│   ├── PS2/
│   └── SNES/
├── assets/
└── config.json                      <- created on first run
```

- `bootloader.bin`, `partition-table.bin` and `ota_data_initial.bin` are the same for every console and are
  already included in each download.
- The **application** `.bin` (any other `.bin` in the folder) is the one that changes. The program can
  download it for you (see below), or you can copy it into the console's folder yourself. If a folder has
  more than one application file, the most recent one is used and a warning is shown.
- To add a console, create a new folder inside `firmware/` with the three base files and the application.

### Firmware updates

Once, when the program starts, it asks GitHub for the releases of
[ReLiveBT-Controller](https://github.com/TecnoBit-Videojuegos/ReLiveBT-Controller/releases) and looks for
files named `ReLiveBT_<CONSOLE>.bin` (for example `ReLiveBT_N64.bin`; `ReLiveBT_GC.bin` is used for the
`GameCube` folder).

- If a published file differs from the one in the console's folder, or the folder has no application yet,
  that console's button shows a small indicator and the text at the bottom right says so.
- Clicking the button opens a dialog with the new version. **Nothing is downloaded until you press
  Download.** The file is verified (size and SHA-256), and your previous file is moved to the `_backup`
  folder inside the console's folder.
- Temporary GitHub errors (such as 502 or 504) are retried automatically.
- With no internet connection the program simply works with the files it already has.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **SIN PROGRAMADOR** (no programmer) | Check the USB cable and that the adapter shows up as a serial port (Windows: Device Manager; Linux: `ls /dev/ttyUSB* /dev/ttyACM*`). On Linux make sure your user is in the `uucp` / `dialout` group. |
| Programmer is plugged in but not detected | Only CH340/CH9102, CP210x and FTDI adapters are recognised automatically. |
| A row says **no hay archivo** | The file is missing from that console's folder. Copy it there or use the firmware update. |
| Flashing fails or stops half-way | Press the PCB's pins firmly against the programmer, then try a lower **Velocidad**. The full esptool output is in **Mostrar registro**. |
| "Could not download: HTTP Error 504" | A temporary GitHub problem. The program already retries; press **Reintentar** a bit later. |
| The window opens off-screen or in a strange place | Delete `config.json` and start again. |

## Building from source

You normally do **not** need this: download a release instead.

To run the tool from the source code you need Python 3.10+ with Tk:

```bash
pip install -r requirements.txt
python relive_flasher.py
```

On Linux install Tk with your package manager first (`sudo pacman -S tk`, `sudo apt install python3-tk`,
`sudo dnf install python3-tkinter`).

To build the standalone executable on your own machine:

| System | Command |
| --- | --- |
| Linux | `./build.sh` |
| Windows | double-click `build.bat` |
| Either | `python build.py` (add `--package` to also create the `.tar.gz` / `.zip`) |

The result is in `dist/ReLive-Flasher/`. Building again keeps the `firmware/` folder, downloads and counters
that are already inside it.

## How releases are made

Nobody compiles by hand. Every push to `main` that changes the program triggers
[GitHub Actions](.github/workflows/build.yml), which:

1. calculates the next version number (`v1.0.1`, `v1.0.2`, ...),
2. builds the Windows and Linux packages on GitHub's own servers,
3. publishes a new release with both downloads, and
4. deletes the previous release, so the Releases page always shows only the latest version.

Changes that only touch documentation or screenshots do not create a release. A build can also be started
manually from the **Actions** tab (*Run workflow*).

Repository layout:

| Path | Purpose |
| --- | --- |
| `relive_flasher.py` | The whole program |
| `build.py`, `build.sh`, `build.bat` | Local build scripts (the same `build.py` is used by GitHub) |
| `requirements.txt` | Exact versions of the dependencies (esptool and pyserial) |
| `consoles.txt` | Consoles that get a `firmware/<Console>/` folder in every download |
| `base-files/` | `bootloader.bin`, `partition-table.bin` and `ota_data_initial.bin` shared by all consoles |
| `assets/` | Program icon |
| `.github/workflows/build.yml` | Automatic build and release |

## License and credits

ReLive Flasher is released under the **GNU General Public License v3.0**, see [LICENSE](LICENSE).
The executable bundles [esptool](https://github.com/espressif/esptool) (GPL-2.0-or-later) and
[pyserial](https://github.com/pyserial/pyserial) (BSD), plus Python and Tk. It is built with
[PyInstaller](https://pyinstaller.org).

© 2026 TecnoBit Videojuegos
