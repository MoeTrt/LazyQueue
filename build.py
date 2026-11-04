"""Construit l'application LazyQueue pour l'OS courant (macOS : LazyQueue.app, Windows : LazyQueue.exe).

Usage :
    python -m venv .venv
    .venv/bin/pip install -r requirements-build.txt      (Windows : .venv\\Scripts\\pip ...)
    .venv/bin/python build.py                             (Windows : .venv\\Scripts\\python build.py)

Résultat dans dist/.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
SEP = os.pathsep


def main():
    args = [
        str(ROOT / "app.py"),
        "--name=LazyQueue",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--exclude-module=PIL",
        f"--add-data={ROOT / 'index.html'}{SEP}.",
        f"--add-data={ROOT / 'static'}{SEP}static",
        f"--icon={ROOT / 'static' / 'icon-512.png'}",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={ROOT / 'build'}",
        f"--specpath={ROOT / 'build'}",
    ]
    if sys.platform == "darwin":
        args.append("--osx-bundle-identifier=app.lazyqueue")
    else:
        args.append("--onefile")
    PyInstaller.__main__.run(args)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    dist = ROOT / "dist"
    if sys.platform == "darwin":
        shutil.rmtree(dist / "LazyQueue", ignore_errors=True)
        archive = dist / "LazyQueue-macOS.zip"
        archive.unlink(missing_ok=True)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(dist / "LazyQueue.app"), str(archive)], check=True)
    else:
        archive = dist / "LazyQueue.exe"
    print(f"\nÀ partager : {archive}")


if __name__ == "__main__":
    main()
