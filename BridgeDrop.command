#!/bin/sh
# Double-click launcher for macOS.
cd "$(dirname "$0")" || exit 1
exec python3 bridgedrop.py
