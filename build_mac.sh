#!/usr/bin/env bash
# Crea "dist/Crypto Radar Ultimate.app" e "dist/Crypto Radar Ultimate.dmg"
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-build.txt
pyinstaller --noconfirm crypto_radar.spec
hdiutil create -volname "Crypto Radar Ultimate" -srcfolder "dist/Crypto Radar Ultimate.app" \
  -ov -format UDZO "dist/Crypto Radar Ultimate.dmg"
echo "Fatto: dist/Crypto Radar Ultimate.dmg"
