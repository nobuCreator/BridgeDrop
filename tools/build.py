"""Packages BridgeDrop into a standalone app that needs no Python, and zips it for distribution.

Needs: pip install -r tools/build-requirements.txt. Writes dist/BridgeDrop-<version>-<system>.zip.
"""
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bridgedrop  # noqa: E402

SYSTEM = {"win32": "windows", "darwin": "mac"}.get(sys.platform, sys.platform)


def main():
    dist = ROOT / "dist"
    command = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "bridgedrop.py"),
        "--name", "BridgeDrop", "--noconfirm", "--clean", "--windowed",
        "--icon", str(ROOT / "assets" / "icon.png"),
        "--add-data", f"{ROOT / 'index.html'}:.",
        "--add-data", f"{ROOT / 'assets'}:assets",
        "--add-data", f"{ROOT / 'LICENSE'}:.",
        "--distpath", str(dist), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    ]
    if bridgedrop.IS_WINDOWS:
        command += ["--add-data", f"{ROOT / 'direct_link.ps1'}:."]
    if bridgedrop.IS_MAC:
        command += ["--osx-bundle-identifier", "app.bridgedrop.desktop"]
    subprocess.run(command, check=True, cwd=ROOT)

    archive = dist / f"BridgeDrop-{bridgedrop.__version__}-{SYSTEM}"
    if bridgedrop.IS_MAC:
        # ditto keeps the app's executable bits and symlinks, which a plain zip would break.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(dist / "BridgeDrop.app"), f"{archive}.zip"], check=True)
        print(dist / "BridgeDrop.app" / "Contents" / "MacOS" / "BridgeDrop")
    else:
        shutil.make_archive(str(archive), "zip", dist, "BridgeDrop")
        print(dist / "BridgeDrop" / "BridgeDrop.exe")
    print(f"{archive}.zip")


if __name__ == "__main__":
    main()
