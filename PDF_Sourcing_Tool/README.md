# PDF Sourcing Tool

Desktop app that takes company websites, finds each company's latest financial
report, downloads the PDF and reads the Balance Sheet period end date (PED).

## Run it (Windows, no commands needed)

Needs Python 3.10 or newer from python.org, installed with "Add python.exe to
PATH" ticked.

Double-click **`run_app.bat`**. The first run creates a private environment
(`.venv`), installs the packages from `requirements.txt` and checks that the
window opens; this needs internet and takes a few minutes. After that the same
file starts the app straight away.

## Run it by hand (any system)

```
python -m venv .venv
.venv\Scripts\activate            (macOS / Linux: source .venv/bin/activate)
pip install -r requirements.txt
python main.py
```

`python main.py --selftest` opens and closes the window once and writes the
outcome to `selftest_result.txt`.

Optional extras (the app runs without them):

```
pip install -r requirements-optional.txt
playwright install chromium       (pages that only appear after JavaScript runs)
```

OCR for scanned PDFs also needs the Tesseract program installed and on PATH.

## Using the window

1. Pick the input: **🔗 Single URL**, or **📊 Excel list** (.xlsx).
2. Choose the folder for the PDFs with **💾 Output Folder**. Start stays disabled until you do.
3. Press **🚀 Start**. **⏹ Stop** ends the run after the current URL.

Phase 1 runs for every URL first (find the investor section, rank the PDF links,
download the best one). Phase 2 then opens each PDF and reads the date, shown as
`DD-MMM-YYYY`. Anything that fails shows `Reports not found`; the exact reason
(no investor page, no PDF, no balance sheet, no date, ...) is in the timestamped
`pdf_sourcing_log_*.log` file in the output folder.

## Excel list

- The URL column is the one whose header contains "url", "link" or "website";
  otherwise the first column where most cells look like URLs.
- `Latest PED` is overwritten if it exists, otherwise added immediately to the
  right of the URL column. `Comments` is reused if it exists, otherwise added
  after the last column.
- On success the date is written and the row's comment is cleared. On failure
  `Reports not found` goes in Comments and Latest PED is left blank.
- Rows with the same URL are fetched once and all get the result.
- Rows not reached because of Stop keep whatever they had.
- If the workbook is open in Excel when saving, close it and click Retry.
  Cancel saves the results as a copy in the output folder instead.

## Build a Windows .exe

Double-click **`build_exe.bat`**. It prepares the environment, installs the
newest PyInstaller and runs `build_exe.py`, which removes any earlier build,
builds `dist\PDF Sourcing Tool.exe` and then starts that .exe once in
self-test mode to prove it opens. If a step fails the window says which one;
`build_log.txt` and `selftest_result.txt` hold the details.

By hand, with the virtual environment active:

```
pip install --upgrade pyinstaller
python build_exe.py
```

Use a current PyInstaller. Newer Python builds keep Tcl/Tk (the toolkit behind
the window) inside their libraries instead of in folders, and PyInstaller
releases before 6.22 produce an .exe that stops with
`Tcl data directory ... _tcl_data not found` on those Pythons.

Set `ONEFILE = False` at the top of `build_exe.py` for a folder build that
starts faster.

## Code layout

| File | Contents |
| --- | --- |
| `main.py` | Entry point (`--selftest` checks the install) |
| `run_app.bat`, `build_exe.bat`, `_setup.bat` | Windows double-click launcher, .exe builder, and the setup they share |
| `build_exe.py` | The PyInstaller build and the self-test of the built .exe |
| `pdf_sourcing/tcl_support.py` | Finds out how Tcl/Tk is installed (used by the build and at start-up) |
| `pdf_sourcing/config.py` | Every keyword list, limit and file name |
| `pdf_sourcing/ui.py` | CustomTkinter window |
| `pdf_sourcing/crawler.py` | `SiteCrawler`: investor section, report sub-pages, PDF links |
| `pdf_sourcing/selector.py` | `ReportSelector` (ranking) and `ReportDownloader` |
| `pdf_sourcing/pdf_parser.py` | `BalanceSheetParser`: Balance Sheet page and date |
| `pdf_sourcing/excel_io.py` | `ExcelUrlList`: reads the list, edits the workbook in place |
| `pdf_sourcing/pipeline.py` | `SourcingWorker`: background thread for both phases |
| `pdf_sourcing/http_client.py` | Session with retries, backoff, delay and robots.txt |
| `pdf_sourcing/renderer.py` | Optional Playwright rendering |
| `pdf_sourcing/dates.py`, `text_utils.py`, `models.py` | Date parsing, URL and keyword helpers, data classes |

## Settings worth knowing (`pdf_sourcing/config.py`)

- `REPORTS_PER_SITE` (default 1): how many top-ranked PDFs to download per
  company. With 2 or 3, Phase 2 moves on to the next report when the newest one
  has no Balance Sheet (common for quarterly results).
- `ALLOW_OFFSITE_PDF_HOSTS` (default True): pages are only crawled on the
  company's own domain and subdomains, but a PDF linked from those pages may be
  downloaded from another host, because many companies keep report files on a CDN.
- `REQUEST_DELAY_SECONDS`, `MAX_PAGES_PER_SITE`: politeness and crawl budget.
- `OCR_MAX_PAGES`: cap on pages read with OCR per PDF.

## Limits

- Adding the `Latest PED` column moves any columns to its right by one. Widths,
  merged cells, hyperlinks and Excel tables are moved with them, but openpyxl
  does not re-point formulas, conditional formatting or data validation that
  refer to the moved columns. This only happens the first time, and only when
  the column next to the URLs is already in use.
- A list with no header row gets one inserted (`URL`, `Latest PED`, `Comments`).
- `Latest PED` is written as text so it always displays as `31-Mar-2026`.
- Sites that block automated visitors, or that keep their investor pages on an
  unrelated domain, end as `Reports not found`.
