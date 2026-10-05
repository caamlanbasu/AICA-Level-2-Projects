"""Tcl/Tk discovery for tkinter.

Python ships Tcl/Tk (the toolkit behind tkinter) in one of two ways:

* "folders": script files on disk, e.g. ``<python>\\tcl\\tcl8.6`` and ``tk8.6``
* "embedded": inside the Tcl/Tk DLLs themselves (Tcl/Tk 9); nothing on disk

Nothing here is needed on a healthy install. It exists so that the build
script can describe what it found, and so that a virtual environment that
cannot locate its Tcl folders is repaired instead of failing.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

KIND_FOLDERS = "folders"
KIND_EMBEDDED = "embedded"
KIND_UNKNOWN = "unknown"
_EMBEDDED_PREFIXES = ("//zipfs:", "zipfs:")


@dataclass
class TclInfo:
    """What tkinter's Tcl/Tk looks like on this machine."""

    kind: str = KIND_UNKNOWN
    patchlevel: str = "?"
    library: str = ""                 # Tcl's own answer to "info library"
    tcl_dir: Optional[Path] = None    # folder with init.tcl   (kind == folders)
    tk_dir: Optional[Path] = None     # folder with tk.tcl     (kind == folders)
    modules_dir: Optional[Path] = None  # sibling "tcl8" folder with Tcl modules, if any
    error: str = ""

    @property
    def summary(self) -> str:
        if self.kind == KIND_EMBEDDED:
            return "data embedded in the Tcl/Tk libraries"
        if self.kind == KIND_FOLDERS:
            return f"data folders {self.tcl_dir} and {self.tk_dir}"
        return f"layout not recognised ({self.error or self.library or 'no details'})"


def _folders_from(tcl_dir: Path) -> Optional[TclInfo]:
    """Build a "folders" description from a Tcl script folder, if it is complete."""
    if not (tcl_dir / "init.tcl").is_file():
        return None
    tk_dirs = sorted(p.parent for p in tcl_dir.parent.glob("tk*/tk.tcl"))
    if not tk_dirs:
        return None
    modules = tcl_dir.parent / "tcl8"
    return TclInfo(
        kind=KIND_FOLDERS,
        tcl_dir=tcl_dir,
        tk_dir=tk_dirs[-1],
        modules_dir=modules if modules.is_dir() else None,
    )


def _scan_python_home() -> Optional[TclInfo]:
    """Look for Tcl folders in the usual places of the Python installation."""
    home = Path(sys.base_prefix)
    for root in (home / "tcl", home / "Library" / "lib", home / "lib"):
        if not root.is_dir():
            continue
        for init in sorted(root.glob("tcl*/init.tcl"), reverse=True):
            found = _folders_from(init.parent)
            if found is not None:
                return found
    return None


def describe_tcl() -> TclInfo:
    """Report how Tcl/Tk is installed. Never raises."""
    try:
        import tkinter

        interpreter = tkinter.Tcl()
        library = str(interpreter.eval("info library"))
        patchlevel = str(interpreter.eval("info patchlevel"))
    except Exception as exc:  # tkinter missing, or Tcl cannot find its files
        found = _scan_python_home()
        if found is not None:
            found.error = f"{type(exc).__name__}: {exc}"
            return found
        return TclInfo(error=f"{type(exc).__name__}: {exc}")

    if library.startswith(_EMBEDDED_PREFIXES):
        return TclInfo(kind=KIND_EMBEDDED, patchlevel=patchlevel, library=library)
    found = _folders_from(Path(library)) or _scan_python_home()
    if found is not None:
        found.patchlevel, found.library = patchlevel, library
        return found
    return TclInfo(patchlevel=patchlevel, library=library)


def ensure_tcl_usable() -> None:
    """Point tkinter at its Tcl folders when it cannot find them on its own.

    Some virtual environments fail with "Can't find a usable init.tcl". If
    that happens and the folders exist in the Python installation, set
    TCL_LIBRARY / TK_LIBRARY so the window can open. Does nothing otherwise.
    """
    if getattr(sys, "frozen", False):
        return  # a built .exe is set up by PyInstaller
    try:
        import tkinter

        tkinter.Tcl()
        return
    except Exception:
        pass
    found = _scan_python_home()
    if found is not None and found.tcl_dir is not None and found.tk_dir is not None:
        os.environ["TCL_LIBRARY"] = str(found.tcl_dir)
        os.environ["TK_LIBRARY"] = str(found.tk_dir)
