# PyInstaller spec - build with:  pyinstaller packaging/igui.spec --noconfirm
# Output: dist/iGui/iGui.exe (one-folder build; copy the whole folder).
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    datas=[(str(SRC / "igui" / "resources"), "igui/resources")],
    hiddenimports=["waitress", "win32com.server.policy", "win32com.server.util"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="iGui",
    console=False,
    icon=str(SRC / "igui" / "resources" / "eco.ico"),
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="iGui")
