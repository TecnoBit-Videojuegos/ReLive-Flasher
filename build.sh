#!/usr/bin/env bash
# Atajo para Linux: construye el ejecutable (ver build.py). Uso:  ./build.sh   (o ./build.sh --package)
cd "$(dirname "$(readlink -f "$0")")" || exit 1
command -v python3 >/dev/null || { echo "Falta python3."; exit 1; }
exec python3 build.py "$@"
