"""Build the Windows .exe with PyInstaller and prove that it starts.

Normally run by build_exe.bat. By hand, with the virtual environment active:

    pip install --upgrade pyinstaller
    python build_exe.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from pdf_sourcing.tcl_support import KIND_EMBEDDED, KIND_FOLDERS, TclInfo, describe_tcl, ensure_tcl_usable

# True  = one single .exe file (easy to copy, starts a little slower)
# False = a folder with the .exe inside (starts faster, keep the folder together)
ONEFILE = True

APP_NAME = "PDF Sourcing Tool"
ROOT = Path(__file__).resolve().parent
BUILD_LOG = ROOT / "build_log.txt"
SELFTEST_REPORT = ROOT / "selftest_result.txt"
SELFTEST_TIMEOUT_SECONDS = 300
MIN_PYINSTALLER_FOR_EMBEDDED_TCL = (6, 22)

# Packages whose data files or binaries PyInstaller does not always pick up by itself.
COLLECT_ALL = ("customtkinter", "pypdfium2", "pypdfium2_raw")
COLLECT_DATA = ("tldextract", "pdfminer", "certifi")
HIDDEN_IMPORTS = ("fitz", "pymupdf")
OPTIONAL_COLLECT_ALL = ("playwright",)
REQUIRED_MODULES = (
    "tkinter", "customtkinter", "requests", "urllib3", "bs4", "lxml", "tldextract",
    "pdfplumber", "fitz", "openpyxl", "dateutil", "PIL",
)


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _version_tuple(text: str) -> Tuple[int, int]:
    numbers = [int(n) for n in re.findall(r"\d+", text)[:2]]
    return (numbers + [0, 0])[0], (numbers + [0, 0])[1]


def _tail(path: Path, lines: int = 25) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log file)"


def _remove_old_builds(exe: Path) -> bool:
    """Delete earlier build output so an old .exe can never be mistaken for the new one."""
    for folder in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(folder, ignore_errors=True)
    spec = ROOT / f"{APP_NAME}.spec"
    if spec.exists():
        spec.unlink()
    return not exe.exists()


def _pyinstaller_command(tcl: TclInfo) -> Tuple[List[str], Dict[str, str]]:
    """PyInstaller arguments and environment for this machine."""
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--onefile" if ONEFILE else "--onedir", "--name", APP_NAME,
    ]
    for package in COLLECT_ALL:
        if _installed(package):
            command += ["--collect-all", package]
    for package in COLLECT_DATA:
        if _installed(package):
            command += ["--collect-data", package]
    for module in HIDDEN_IMPORTS:
        if _installed(module):
            command += ["--hidden-import", module]
    for package in OPTIONAL_COLLECT_ALL:
        if _installed(package):
            command += ["--collect-all", package]
            print(f"      optional: {package} found, it will be included")

    environment = os.environ.copy()
    if tcl.kind == KIND_FOLDERS:
        # Tell PyInstaller exactly where the Tcl/Tk script folders are, and add them
        # explicitly as well. With embedded Tcl/Tk there is nothing to add.
        environment["TCL_LIBRARY"] = str(tcl.tcl_dir)
        environment["TK_LIBRARY"] = str(tcl.tk_dir)
        command += ["--add-data", f"{tcl.tcl_dir}{os.pathsep}_tcl_data"]
        command += ["--add-data", f"{tcl.tk_dir}{os.pathsep}_tk_data"]
        if tcl.modules_dir is not None:
            command += ["--add-data", f"{tcl.modules_dir}{os.pathsep}tcl8"]
    command.append("main.py")
    return command, environment


def _test_exe(exe: Path) -> Tuple[bool, str]:
    """Start the new .exe in self-test mode and read its report."""
    if SELFTEST_REPORT.exists():
        SELFTEST_REPORT.unlink()
    environment = {k: v for k, v in os.environ.items() if k.upper() not in ("TCL_LIBRARY", "TK_LIBRARY")}
    try:
        subprocess.run([str(exe), "--selftest"], cwd=str(ROOT), env=environment, timeout=SELFTEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, f"the .exe did not finish its self-test within {SELFTEST_TIMEOUT_SECONDS} seconds"
    except OSError as exc:
        return False, f"the .exe could not be started: {exc}"
    try:
        report = SELFTEST_REPORT.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False, "the .exe closed without writing selftest_result.txt (it failed before the app code ran)"
    return report.startswith("OK"), report


def main() -> int:
    os.chdir(ROOT)

    missing = [module for module in REQUIRED_MODULES if not _installed(module)]
    if missing:
        print(f"PROBLEM: these packages are not installed: {', '.join(missing)}")
        print("Delete the .venv folder and run build_exe.bat again.")
        return 1
    if not _installed("PyInstaller"):
        print("PROBLEM: PyInstaller is not installed. Run:  pip install --upgrade pyinstaller")
        return 1
    import PyInstaller

    ensure_tcl_usable()
    tcl = describe_tcl()
    print(f"      Python {sys.version.split()[0]}, PyInstaller {PyInstaller.__version__}")
    print(f"      Tcl/Tk {tcl.patchlevel}: {tcl.summary}")
    if tcl.kind == KIND_EMBEDDED and _version_tuple(PyInstaller.__version__) < MIN_PYINSTALLER_FOR_EMBEDDED_TCL:
        wanted = ".".join(str(n) for n in MIN_PYINSTALLER_FOR_EMBEDDED_TCL)
        print(f"PROBLEM: this Python keeps Tcl/Tk inside its libraries, which needs PyInstaller {wanted} or newer.")
        print("Run:  pip install --upgrade pyinstaller")
        return 1

    exe = ROOT / "dist" / (f"{APP_NAME}.exe" if ONEFILE else f"{APP_NAME}/{APP_NAME}.exe")
    if not _remove_old_builds(exe):
        print(f'PROBLEM: the old "{exe.name}" could not be removed.')
        print("Close the application and any error message from it, then run this again.")
        return 1

    command, environment = _pyinstaller_command(tcl)
    print("[3/4] Building the .exe. This takes a few minutes, details go to build_log.txt ...")
    with open(BUILD_LOG, "w", encoding="utf-8", errors="replace") as log:
        log.write("COMMAND: " + " ".join(f'"{part}"' if " " in part else part for part in command) + "\n\n")
        log.flush()
        result = subprocess.run(command, cwd=str(ROOT), env=environment, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode != 0 or not exe.is_file():
        print("PROBLEM: PyInstaller did not produce the .exe. Last lines of build_log.txt:")
        print("-" * 60)
        print(_tail(BUILD_LOG))
        print("-" * 60)
        print("Please send build_log.txt.")
        return 1

    print("[4/4] Testing the new .exe (a window opens and closes by itself).")
    print("      If an error box appears, click OK so the test can finish.")
    passed, report = _test_exe(exe)
    if not passed:
        print("PROBLEM: the .exe was built but did not start correctly:")
        print(report)
        print("Please send build_log.txt, and selftest_result.txt if it exists.")
        return 1

    print("      " + report.replace("\n", "\n      "))
    print()
    print("=" * 60)
    print("  Build finished and tested.")
    print(f"  Your application: {exe}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
