"""Load the cleaned tables into a SQLite star schema (data/northbrook.db).

Facts:      fact_orders (one row per order), fact_order_lines, fact_inventory
Dimensions: dim_date, dim_customer, dim_product, dim_carrier, dim_region
"""
import sqlite3

import pandas as pd

from config import CARRIER_RATES, DB_PATH, PROCESSED, REMOTE_REGIONS

SCHEMA = """
DROP TABLE IF EXISTS fact_order_lines;
DROP TABLE IF EXISTS fact_orders;
DROP TABLE IF EXISTS fact_inventory;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_carrier;
DROP TABLE IF EXISTS dim_region;

CREATE TABLE dim_date (
    date_key INTEGER PRIMARY KEY, date TEXT, year INTEGER, quarter INTEGER, month INTEGER,
    month_name TEXT, iso_week INTEGER, day_name TEXT, is_weekend INTEGER, is_peak INTEGER);
CREATE TABLE dim_region (region_key INTEGER PRIMARY KEY, region TEXT UNIQUE, is_remote INTEGER);
CREATE TABLE dim_carrier (carrier_key INTEGER PRIMARY KEY, carrier TEXT UNIQUE, rate_per_parcel REAL);
CREATE TABLE dim_customer (
    customer_key INTEGER PRIMARY KEY, customer_id TEXT UNIQUE, region_key INTEGER,
    signup_date TEXT, preferred_channel TEXT,
    FOREIGN KEY (region_key) REFERENCES dim_region(region_key));
CREATE TABLE dim_product (
    product_key INTEGER PRIMARY KEY, sku TEXT UNIQUE, category TEXT, product_name TEXT,
    unit_price REAL, unit_cost REAL);
CREATE TABLE fact_orders (
    order_id TEXT PRIMARY KEY, customer_key INTEGER, order_date_key INTEGER,
    region_key INTEGER, carrier_key INTEGER, channel TEXT, status TEXT,
    order_hour INTEGER, after_cutoff INTEGER, has_backorder INTEGER, n_items INTEGER,
    order_value REAL, shipping_fee REAL, promised_date TEXT, dispatch_datetime TEXT,
    delivered_datetime TEXT, is_late REAL, days_late REAL, lead_time_days REAL,
    FOREIGN KEY (customer_key) REFERENCES dim_customer(customer_key),
    FOREIGN KEY (order_date_key) REFERENCES dim_date(date_key),
    FOREIGN KEY (region_key) REFERENCES dim_region(region_key),
    FOREIGN KEY (carrier_key) REFERENCES dim_carrier(carrier_key));
CREATE TABLE fact_order_lines (
    order_id TEXT, product_key INTEGER, quantity INTEGER, unit_price REAL, backordered INTEGER,
    FOREIGN KEY (order_id) REFERENCES fact_orders(order_id),
    FOREIGN KEY (product_key) REFERENCES dim_product(product_key));
CREATE TABLE fact_inventory (
    snapshot_date TEXT, product_key INTEGER, stock_on_hand INTEGER,
    FOREIGN KEY (product_key) REFERENCES dim_product(product_key));
CREATE INDEX ix_orders_date ON fact_orders(order_date_key);
CREATE INDEX ix_orders_carrier ON fact_orders(carrier_key);
CREATE INDEX ix_lines_order ON fact_order_lines(order_id);
"""


def date_key(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y%m%d").astype(int)


def main():
    orders = pd.read_csv(PROCESSED / "orders_clean.csv", parse_dates=["order_datetime"])
    lines = pd.read_csv(PROCESSED / "order_lines_clean.csv")
    customers = pd.read_csv(PROCESSED / "customers_clean.csv")
    products = pd.read_csv(PROCESSED / "products_clean.csv")
    inventory = pd.read_csv(PROCESSED / "inventory_clean.csv")

    dates = pd.DataFrame({"date": pd.date_range(orders["order_datetime"].min().normalize(),
                                                "2026-03-31", freq="D")})
    dim_date = pd.DataFrame({
        "date_key": date_key(dates["date"]), "date": dates["date"].dt.strftime("%Y-%m-%d"),
        "year": dates["date"].dt.year, "quarter": dates["date"].dt.quarter,
        "month": dates["date"].dt.month, "month_name": dates["date"].dt.month_name(),
        "iso_week": dates["date"].dt.isocalendar().week.astype(int),
        "day_name": dates["date"].dt.day_name(),
        "is_weekend": (dates["date"].dt.dayofweek >= 5).astype(int),
        "is_peak": (((dates["date"].dt.month == 11) & (dates["date"].dt.day >= 15)) |
                    ((dates["date"].dt.month == 12) & (dates["date"].dt.day <= 20))).astype(int),
    })
    regions = sorted(orders["region"].unique())
    dim_region = pd.DataFrame({"region_key": range(1, len(regions) + 1), "region": regions})
    dim_region["is_remote"] = dim_region["region"].isin(REMOTE_REGIONS).astype(int)
    dim_carrier = pd.DataFrame({"carrier_key": range(1, len(CARRIER_RATES) + 1),
                                "carrier": list(CARRIER_RATES), "rate_per_parcel": list(CARRIER_RATES.values())})
    rk = dict(zip(dim_region["region"], dim_region["region_key"]))
    ck = dict(zip(dim_carrier["carrier"], dim_carrier["carrier_key"]))

    dim_customer = customers.assign(customer_key=range(1, len(customers) + 1),
                                    region_key=customers["region"].map(rk))
    dim_customer = dim_customer[["customer_key", "customer_id", "region_key", "signup_date", "preferred_channel"]]
    cust_k = dict(zip(dim_customer["customer_id"], dim_customer["customer_key"]))
    dim_product = products.assign(product_key=range(1, len(products) + 1))[
        ["product_key", "sku", "category", "product_name", "unit_price", "unit_cost"]]
    pk = dict(zip(dim_product["sku"], dim_product["product_key"]))

    fact_orders = pd.DataFrame({
        "order_id": orders["order_id"], "customer_key": orders["customer_id"].map(cust_k),
        "order_date_key": date_key(orders["order_date"]), "region_key": orders["region"].map(rk),
        "carrier_key": orders["carrier"].map(ck), "channel": orders["channel"], "status": orders["status"],
        "order_hour": orders["order_hour"], "after_cutoff": orders["after_cutoff"],
        "has_backorder": orders["has_backorder"], "n_items": orders["n_items"],
        "order_value": orders["order_value"], "shipping_fee": orders["shipping_fee"],
        "promised_date": orders["promised_date"], "dispatch_datetime": orders["dispatch_datetime"],
        "delivered_datetime": orders["delivered_datetime"], "is_late": orders["is_late"],
        "days_late": orders["days_late"], "lead_time_days": orders["lead_time_days"],
    })
    fact_lines = pd.DataFrame({"order_id": lines["order_id"], "product_key": lines["sku"].map(pk),
                               "quantity": lines["quantity"], "unit_price": lines["unit_price"],
                               "backordered": lines["backordered"]})
    fact_inv = pd.DataFrame({"snapshot_date": inventory["snapshot_date"],
                             "product_key": inventory["sku"].map(pk),
                             "stock_on_hand": inventory["stock_on_hand"]})

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(SCHEMA)
        for name, df in [("dim_date", dim_date), ("dim_region", dim_region), ("dim_carrier", dim_carrier),
                         ("dim_customer", dim_customer), ("dim_product", dim_product),
                         ("fact_orders", fact_orders), ("fact_order_lines", fact_lines),
                         ("fact_inventory", fact_inv)]:
            df.to_sql(name, con, if_exists="append", index=False)
            print(f"{name:<18} {len(df):>8,} rows")


if __name__ == "__main__":
    main()
