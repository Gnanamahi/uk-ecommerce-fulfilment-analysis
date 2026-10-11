"""Clean the raw exports and write analysis-ready tables to data/processed.

Every fix is logged to reports/data_quality_log.csv so the cleaning is
auditable: a client can see exactly how many rows each rule touched.
"""
import numpy as np
import pandas as pd

from config import PROCESSED, RAW, REMOTE_REGIONS, REPORTS, DISPATCH_CUTOFF_HOUR

REGION_MAP = {
    "london": "London", "greater london": "London",
    "scotland": "Scotland",
    "north-west": "North West", "nw": "North West", "north west": "North West",
    "n. ireland": "Northern Ireland", "ni": "Northern Ireland", "northern ireland": "Northern Ireland",
}
CARRIER_MAP = {"budgetpost": "BudgetPost", "swiftparcel": "SwiftParcel", "parcellink": "ParcelLink"}

log = []


def note(rule: str, rows: int, action: str):
    log.append({"rule": rule, "rows_affected": int(rows), "action": action})


def parse_mixed_datetime(s: pd.Series) -> pd.Series:
    """Parse ISO (yyyy-mm-dd) and UK (dd/mm/yyyy) strings in the same column."""
    iso = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    uk = pd.to_datetime(s, format="%d/%m/%Y %H:%M", errors="coerce")
    note(f"uk_date_format:{s.name}", (iso.isna() & uk.notna()).sum(), "parsed dd/mm/yyyy explicitly (avoids US month swap)")
    return iso.fillna(uk)


def standardise_region(s: pd.Series) -> pd.Series:
    cleaned = s.str.strip()
    mapped = cleaned.str.lower().map(REGION_MAP)
    out = mapped.fillna(cleaned)
    note("region_labels", (out != s).sum(), "mapped variants to 10 canonical UK regions")
    return out


def clean_orders(o: pd.DataFrame) -> pd.DataFrame:
    n0 = len(o)
    o = o.drop_duplicates()
    note("exact_duplicates", n0 - len(o), "dropped")
    n1 = len(o)
    o = o.drop_duplicates(subset="order_id", keep="first")
    note("duplicate_order_id", n1 - len(o), "kept first occurrence")

    o["region"] = standardise_region(o["region"])
    key = o["carrier"].str.replace(" ", "").str.lower()
    note("carrier_labels", (key.map(CARRIER_MAP) != o["carrier"]).sum(), "mapped to 3 canonical carriers")
    o["carrier"] = key.map(CARRIER_MAP)

    st = o["status"].str.title()
    note("status_casing", (st != o["status"]).sum(), "title-cased")
    o["status"] = st

    for col in ["order_datetime", "dispatch_datetime", "delivered_datetime"]:
        o[col] = parse_mixed_datetime(o[col].rename(col))
    o["promised_date"] = pd.to_datetime(o["promised_date"])

    bad_val = o["order_value"].isna() | (o["order_value"] <= 0)
    note("invalid_order_value", bad_val.sum(), "set to NaN, excluded from revenue metrics")
    o.loc[bad_val, "order_value"] = np.nan

    impossible = o["delivered_datetime"] < o["dispatch_datetime"]
    note("delivered_before_dispatch", impossible.sum(), "delivery timestamp nulled (scanner clock error)")
    o.loc[impossible, "delivered_datetime"] = pd.NaT

    missing_scan = (o["status"] == "Delivered") & o["delivered_datetime"].isna()
    note("missing_delivery_scan", missing_scan.sum(), "kept; excluded from on-time metrics")

    # derived fields
    o["order_date"] = o["order_datetime"].dt.normalize()
    o["order_hour"] = o["order_datetime"].dt.hour
    o["order_dow"] = o["order_datetime"].dt.day_name()
    o["after_cutoff"] = (o["order_hour"] >= DISPATCH_CUTOFF_HOUR).astype(int)
    o["is_remote"] = o["region"].isin(REMOTE_REGIONS).astype(int)
    m, d = o["order_datetime"].dt.month, o["order_datetime"].dt.day
    o["is_peak"] = (((m == 11) & (d >= 15)) | ((m == 12) & (d <= 20))).astype(int)
    o["delivered_date"] = o["delivered_datetime"].dt.normalize()
    o["measurable"] = o["delivered_date"].notna() & o["status"].isin(["Delivered", "Returned"])
    o["is_late"] = np.where(o["measurable"], (o["delivered_date"] > o["promised_date"]).astype(float), np.nan)
    o["days_late"] = np.where(o["measurable"], (o["delivered_date"] - o["promised_date"]).dt.days.clip(lower=0), np.nan)
    o["lead_time_days"] = (o["delivered_date"] - o["order_date"]).dt.days
    return o.sort_values("order_datetime").reset_index(drop=True)


def main():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    orders = pd.read_csv(RAW / "orders.csv")
    lines = pd.read_csv(RAW / "order_lines.csv")
    customers = pd.read_csv(RAW / "customers.csv")
    products = pd.read_csv(RAW / "products.csv")
    inventory = pd.read_csv(RAW / "inventory_snapshot.csv")

    orders = clean_orders(orders)

    stockout = lines.groupby("order_id")["backordered"].max().rename("has_backorder")
    orders = orders.merge(stockout, on="order_id", how="left")
    orders["has_backorder"] = orders["has_backorder"].fillna(0).astype(int)

    orphan = ~lines["order_id"].isin(orders["order_id"])
    note("orphan_order_lines", orphan.sum(), "dropped")
    lines = lines[~orphan]

    # missing customer regions can be recovered from their orders
    missing = customers["region"].isna()
    first_region = orders.groupby("customer_id")["region"].first()
    customers.loc[missing, "region"] = customers.loc[missing, "customer_id"].map(first_region)
    note("customer_region_missing", missing.sum(), "back-filled from the customer's order region")

    orders.to_csv(PROCESSED / "orders_clean.csv", index=False)
    lines.to_csv(PROCESSED / "order_lines_clean.csv", index=False)
    customers.to_csv(PROCESSED / "customers_clean.csv", index=False)
    products.to_csv(PROCESSED / "products_clean.csv", index=False)
    inventory.to_csv(PROCESSED / "inventory_clean.csv", index=False)

    REPORTS.mkdir(exist_ok=True)
    pd.DataFrame(log).to_csv(REPORTS / "data_quality_log.csv", index=False)
    print(pd.DataFrame(log).to_string(index=False))
    print(f"\nclean orders: {len(orders):,}")


if __name__ == "__main__":
    main()
