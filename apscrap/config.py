import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "user": os.getenv("PGUSER", "apscrap"),
    "password": os.getenv("PGPASSWORD", "apscrap"),
    "dbname": os.getenv("PGDATABASE", "apscrap"),
}

DSN = "postgresql://{user}:{password}@{host}:{port}/{dbname}".format(**PG)

# Politeness settings. Raising the delay costs you nothing but wall-clock time,
# and a crawl that finishes in 40 minutes instead of 20 is still a crawl.
DELAY_SECONDS = float(os.getenv("APSCRAP_DELAY", "3.0"))
TIMEOUT_SECONDS = float(os.getenv("APSCRAP_TIMEOUT", "30"))

USER_AGENT = os.getenv(
    "APSCRAP_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SITE_SRC = Path(__file__).resolve().parent.parent / "site"

# Detail pages are the only source of coordinates. 150 a night is the steady
# state; raise it for the first weeks so the backlog of rentals clears.
DETAIL_LIMIT = int(os.getenv("APSCRAP_DETAIL_LIMIT", "150"))
# Raw search pages older than this are deleted once parsed. The normalised
# tables keep the history; this only limits how far back a parser fix can
# replay attribute changes. Detail pages are fetched once and kept forever.
RAW_SEARCH_KEEP_DAYS = int(os.getenv("APSCRAP_RAW_KEEP_DAYS", "60"))
