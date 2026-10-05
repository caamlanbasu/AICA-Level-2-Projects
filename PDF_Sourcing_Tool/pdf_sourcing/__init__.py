"""PDF Sourcing Tool: finds the latest financial report on company websites,
downloads the PDF and reads the Balance Sheet period end date.

Modules
-------
config       keyword lists, limits and paths
ui           CustomTkinter window
crawler      SiteCrawler: investor section and PDF links
selector     ReportSelector (ranking) and ReportDownloader
pdf_parser   BalanceSheetParser: Balance Sheet page and period end date
excel_io     ExcelUrlList: reads the URL list, writes results in place
pipeline     SourcingWorker: background thread that runs both phases
"""

from .config import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
__version__ = APP_VERSION
