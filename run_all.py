"""Run the full Northbrook Home pipeline end to end:  python run_all.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import build_warehouse, clean, dashboard, drivers_model, forecast, generate_data, opportunity_sizing, run_queries  # noqa: E402

STEPS = [
    ("1/8 Generate simulated raw data", generate_data.main),
    ("2/8 Clean and validate", clean.main),
    ("3/8 Build SQLite star schema", build_warehouse.main),
    ("4/8 Run SQL business queries", run_queries.main),
    ("5/8 Fit late-delivery driver model", drivers_model.main),
    ("6/8 Forecast weekly demand", forecast.main),
    ("7/8 Size the opportunity (+ Monte Carlo)", opportunity_sizing.main),
    ("8/8 Build dashboard", dashboard.main),
]

if __name__ == "__main__":
    t0 = time.time()
    for title, step in STEPS:
        print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
        step()
    print(f"\nDone in {time.time() - t0:.0f}s. Open reports/dashboard.html")
