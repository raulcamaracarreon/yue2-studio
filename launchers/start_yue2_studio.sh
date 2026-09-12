#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="${YUE2_STUDIO_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
YUE2_PYTHON="${YUE2_PYTHON:-$HOME/venvs/yue2/bin/python}"
cd "$REPO_DIR"
exec "$YUE2_PYTHON" app.py
