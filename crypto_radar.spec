# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller --noconfirm crypto_radar.spec
# Windows -> dist/Crypto Radar Ultimate.exe
# macOS   -> dist/Crypto Radar Ultimate.app
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [("trend_crypto.py", ".")]
binaries = []
# trend_crypto.py viene eseguito da Streamlit come file, quindi le sue
# dipendenze vanno dichiarate esplicitamente.
hiddenimports = ["trend_crypto", "ccxt", "feedparser", "openpyxl", "altair", "requests"]
for pkg in ("streamlit", "altair"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += copy_metadata("streamlit")

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = "assets/icon.ico" if sys.platform == "win32" else "assets/icon.icns"

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Crypto Radar Ultimate",
              console=False, icon=icon)
    coll = COLLECT(exe, a.binaries, a.datas, name="Crypto Radar Ultimate")
    app = BUNDLE(coll, name="Crypto Radar Ultimate.app", icon=icon,
                 bundle_identifier="com.cryptoradar.ultimate",
                 info_plist={"NSHighResolutionCapable": True})
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Crypto Radar Ultimate",
              console=False, icon=icon, upx=False)
