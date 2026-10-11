"""Shared paths and business assumptions for the Northbrook Home fulfilment case.

Every £ figure in the analysis traces back to an assumption in this file, so a
reviewer can challenge or change one number and re-run the whole pipeline.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "northbrook.db"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
SQL_DIR = ROOT / "sql"

SEED = 42
WARMUP_START = "2022-07-01"         # simulation burn-in, not analysed
START_DATE = "2023-01-01"
END_DATE = "2025-12-31"
CARRIER_SWITCH_DATE = "2024-07-01"   # BudgetPost introduced as main carrier

# --- Operations -------------------------------------------------------------
DISPATCH_CUTOFF_HOUR = 14          # orders before 14:00 Mon-Fri ship same day
PROMISE_WORKING_DAYS = 3           # "delivered within 3 working days"
REMOTE_REGIONS = ["Scotland", "Northern Ireland", "Wales"]

# Per-parcel carrier rates (£)
CARRIER_RATES = {"SwiftParcel": 4.10, "ParcelLink": 3.60, "BudgetPost": 2.85}

# --- Cost of a late delivery -------------------------------------------------
GROSS_MARGIN = 0.38                # gross margin on order value
CS_CONTACT_RATE = 0.35             # share of late orders that contact support
CS_CONTACT_COST = 4.50             # £ per support contact
GOODWILL_RATE = 0.20               # share of late orders given a voucher
GOODWILL_COST = 5.00               # £ per voucher

# --- Intervention costs (annual, £) -----------------------------------------
LATE_SHIFT_COST = 2 * 2.0 * 14.50 * 253 + 22 * 253   # 2 pickers x 2h x £14.50 + late collection fee
WEEKEND_SHIFT_COST = 4 * 8.0 * 19.00 * 52 + 45 * 52  # 4 staff x 8h Saturday premium + collection
INVENTORY_LEAD_TIME_WEEKS = 4
INVENTORY_REVIEW_WEEKS = 2
SERVICE_LEVEL_Z = 1.65             # ~95% cycle service level
HOLDING_COST_RATE = 0.20           # annual holding cost as share of stock value
