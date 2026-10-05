"""PDF Sourcing Tool: start the desktop application.

Run with:            python main.py
Check the install:   python main.py --selftest
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

SELFTEST_FLAG = "--selftest"
SELFTEST_REPORT = "selftest_result.txt"


def _selftest() -> int:
    """Load every dependency and open the window once, then close it.

    Writes ``selftest_result.txt`` in the current folder: a line starting
    with "OK" on success, otherwise the full error. Used by run_app.bat and
    build_exe.bat to prove that the app (or the built .exe) really starts.
    """
    report = Path.cwd() / SELFTEST_REPORT
    try:
        from pdf_sourcing.tcl_support import describe_tcl, ensure_tcl_usable

        ensure_tcl_usable()
        import customtkinter

        from pdf_sourcing import ui
        from pdf_sourcing.config import APP_NAME, APP_VERSION

        app = ui.App()
        app.update()
        app._on_close()  # noqa: SLF001 - same path as closing the window by hand
        tcl = describe_tcl()
        text = (
            f"OK {APP_NAME} {APP_VERSION}\n"
            f"python {sys.version.split()[0]} | tcl/tk {tcl.patchlevel} ({tcl.kind}) | "
            f"customtkinter {getattr(customtkinter, '__version__', '?')} | "
            f"built exe: {bool(getattr(sys, 'frozen', False))}\n"
        )
        code = 0
    except BaseException:  # the report must be written whatever goes wrong
        text = "FAILED\n" + traceback.format_exc()
        code = 1
    try:
        report.write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.stderr is not None and code:
        print(text, file=sys.stderr)
    return code


def main() -> int:
    """Launch the window. Returns a process exit code."""
    if SELFTEST_FLAG in sys.argv[1:]:
        return _selftest()
    try:
        from pdf_sourcing.tcl_support import ensure_tcl_usable

        ensure_tcl_usable()
        from pdf_sourcing.ui import run_app
    except ImportError as exc:
        print(
            f"A required package is missing ({exc.name or exc}).\n"
            "Install the dependencies first:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    run_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
