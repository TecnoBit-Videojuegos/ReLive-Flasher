# ReLive Flasher

[![Latest release](https://img.shields.io/github/v/release/TecnoBit-Videojuegos/ReLive-Flasher?label=download&color=4f8cff)](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/releases/latest)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-lightgrey)](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/releases/latest)

A simple desktop tool to program the boards of the **ReLiveBT** Bluetooth controllers (GameCube, N64, PS1, PS2 and SNES). Pick a console, place the PCB on the programmer and press a key. No command line, no Python, nothing to install.

The firmware itself lives in [TecnoBit-Videojuegos/ReLiveBT-Controller](https://github.com/TecnoBit-Videojuegos/ReLiveBT-Controller). This repository is only the programming tool.

> The program interface is in Spanish.

<p align="center">
  <img src="docs/screenshots/main.png" alt="ReLive Flasher main window" width="600">
</p>

## What it does

- **Programs a board in one click**, or with any key press.
- **One button per console.** The program shows exactly which files are ready and which are missing.
- **Manual mode:** you place the PCB and press a key.
- **Automatic mode:** made for production. It detects a new PCB, programs it, and waits until you remove it before the next one.
- **Finds the USB programmer by itself** (CH340/CH9102, CP210x and FTDI adapters).
- **Counts the PCBs** you have programmed for each console.
- **Tells you when there is new firmware.** It checks GitHub once at startup. Nothing is downloaded unless you say so.
- **Works offline** with the files you already have.

## Download

Get the latest version from the **[Releases page](https://github.com/TecnoBit-Videojuegos/ReLive-Flasher/releases/latest)**.

| System | File | How to run |
| --- | --- | --- |
| Windows (64-bit) | `ReLive-Flasher-windows-x64.zip` | Extract it and run `ReLive-Flasher.exe` |
| Linux (x86-64) | `ReLive-Flasher-linux-x64.tar.gz` | Extract it and run `ReLive-Flasher/ReLive-Flasher` |

Keep the extracted folder together. The firmware, your settings and the PCB counters are stored inside it, so you can move it anywhere.

**Windows:** the first time, SmartScreen may say *"Windows protected your PC"*. Click **More info → Run anyway**. If the programmer does not appear as a COM port, install the driver from the chip maker (WCH for CH340/CH9102, Silicon Labs for CP210x, FTDI for FTDI).

**Linux:** your user needs permission to use serial ports. Run one of these, then log out and in again:

- Arch / CachyOS / Manjaro: `sudo usermod -aG uucp $USER`
- Debian / Ubuntu / Fedora: `sudo usermod -aG dialout $USER`

## How to use it

1. Plug in the USB programmer. It is detected automatically.
2. Choose the console with the buttons at the top.
3. Check the **Archivos a grabar** card. Every file needs a green check.
4. Place the PCB on the programmer and press **PROGRAMAR**, or just press any key.
5. Watch the progress bar until the **LISTO** banner appears.

For several boards in a row, turn on **Modo automático** and just swap PCBs.

<p align="center">
  <img src="docs/screenshots/flashing.png" alt="Programming a board" width="45%">
  <img src="docs/screenshots/done.png" alt="Board programmed successfully" width="45%">
</p>

**Velocidad** sets the programming speed. If flashing is unstable because of long cables or poor contact, choose a lower value.

## Firmware updates

When the program starts, it checks whether a newer firmware is published for each console. If so, that console's button shows a small indicator and a message appears at the bottom. Click the button to see the new version and choose whether to download it.

The download is verified, and your previous file is kept in a `_backup` folder, so you can always go back.

<p align="center">
  <img src="docs/screenshots/update-available.png" alt="Firmware update available" width="600">
</p>

If a console has no firmware yet, the program says what is missing and you can download it from the same place.

<p align="center">
  <img src="docs/screenshots/missing-firmware.png" alt="Console without firmware" width="600">
</p>

## When something goes wrong

If a board cannot be programmed, the program shows the error in red and lets you try again. Press **Mostrar registro** to see the full detail.

<p align="center">
  <img src="docs/screenshots/error.png" alt="Error while programming" width="600">
</p>

| Symptom | What to check |
| --- | --- |
| **SIN PROGRAMADOR** | Check the USB cable. On Linux, make sure your user is in the `uucp` / `dialout` group. |
| Programmer plugged in but not detected | Only CH340/CH9102, CP210x and FTDI adapters are recognized automatically. |
| A row says **no hay archivo** | The file is missing for that console. Download it with the firmware update, or copy it into the console's folder. |
| Programming fails or stops half-way | Press the PCB pins firmly against the programmer and try a lower **Velocidad**. |
| "Could not download: HTTP Error 504" | A temporary GitHub problem. Press **Reintentar** a bit later. |
| The window opens in a strange place | Delete `config.json` and open the program again. |

## License and credits

Released under the **GNU General Public License v3.0**, see [LICENSE](LICENSE). It bundles [esptool](https://github.com/espressif/esptool) (GPL-2.0-or-later) and [pyserial](https://github.com/pyserial/pyserial) (BSD).

© 2026 TecnoBit Videojuegos
