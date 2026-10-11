"""Tests for the Northbrook pipeline.  Run with:  pytest -q

If the pipeline outputs are missing, the session fixture runs it once first.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clean import parse_mixed_datetime, standardise_region  # noqa: E402
from config import DB_PATH, PROCESSED, REPORTS  # noqa: E402
from run_queries import load_queries  # noqa: E402

CANON_REGIONS = {"London", "South East", "Midlands", "North West", "Yorkshire", "South West",
                 "North East", "Scotland", "Wales", "Northern Ireland"}


@pytest.fixture(scope="session", autouse=True)
def pipeline_outputs():
    if not (REPORTS / "headline_summary.csv").exists():
        import run_all  # noqa: F401  (imports only; run steps explicitly)
        for _, step in run_all.STEPS[:-1]:
            step()


@pytest.fixture(scope="session")
def orders():
    return pd.read_csv(PROCESSED / "orders_clean.csv",
                       parse_dates=["order_datetime", "dispatch_datetime", "delivered_datetime",
                                    "promised_date", "delivered_date"])


# ---------- unit tests on cleaning helpers ----------
def test_mixed_dates_parse_uk_format_without_month_swap():
    s = pd.Series(["2025-03-04 10:00:00", "04/03/2025 10:00"], name="t")
    out = parse_mixed_datetime(s)
    assert (out == pd.Timestamp("2025-03-04 10:00")).all()


def test_region_variants_map_to_canonical():
    s = pd.Series(["london", "LONDON ", "Greater London", "NW", "N. Ireland", "Wales"])
    assert standardise_region(s).tolist() == ["London", "London", "London", "North West",
                                              "Northern Ireland", "Wales"]


# ---------- data quality of processed tables ----------
def test_order_ids_unique(orders):
    assert orders["order_id"].is_unique


def test_labels_are_canonical(orders):
    assert set(orders["region"]) <= CANON_REGIONS
    assert set(orders["carrier"]) == {"SwiftParcel", "ParcelLink", "BudgetPost"}
    assert set(orders["status"]) <= {"Delivered", "Returned", "Cancelled"}


def test_no_delivery_before_dispatch(orders):
    both = orders.dropna(subset=["dispatch_datetime", "delivered_datetime"])
    assert (both["delivered_datetime"] >= both["dispatch_datetime"]).all()


def test_late_flag_matches_dates(orders):
    m = orders[orders["is_late"].notna()]
    assert ((m["delivered_date"] > m["promised_date"]).astype(float) == m["is_late"]).all()


# ---------- warehouse and SQL ----------
def test_warehouse_matches_processed(orders):
    with sqlite3.connect(DB_PATH) as con:
        n = con.execute("SELECT COUNT(*) FROM fact_orders").fetchone()[0]
        orphans = con.execute("""SELECT COUNT(*) FROM fact_orders f
                                 LEFT JOIN dim_customer c ON c.customer_key = f.customer_key
                                 WHERE c.customer_key IS NULL""").fetchone()[0]
    assert n == len(orders)
    assert orphans == 0


def test_all_sql_queries_return_rows():
    queries = load_queries()
    assert len(queries) == 8
    with sqlite3.connect(DB_PATH) as con:
        for name, sql in queries.items():
            assert len(pd.read_sql_query(sql, con)) > 0, name


# ---------- analytical results ----------
def test_model_beats_chance_on_holdout():
    assert pd.read_csv(REPORTS / "model_metrics.csv")["holdout_auc"].iloc[0] > 0.75


def test_holt_winters_beats_seasonal_naive():
    s = pd.read_csv(REPORTS / "forecast_backtest.csv").set_index("model")["WAPE_%"]
    assert s["Holt-Winters"] < s["Seasonal naive"]


def test_recommendations_are_consistent():
    lv = pd.read_csv(REPORTS / "opportunity_levers.csv").set_index("lever")
    h = pd.read_csv(REPORTS / "headline_summary.csv").iloc[0]
    assert lv.loc["E", "net_benefit_gbp"] < 0          # Saturday shift loses money
    assert h["plan_net_benefit_gbp"] > 0
    assert h["otd_plan_pct"] > h["otd_2025_pct"]
