"""Generate a realistic, seeded dataset for the fictional retailer Northbrook Home.

The data is simulated (no real customer data is used) but it is built from
explicit operational rules, so the causes of late delivery are known and the
analysis can show it recovers them:

* dispatch cut-off at 14:00, no weekend dispatch, UK bank holidays
* a carrier switch in July 2024 to a cheaper, slower carrier (BudgetPost)
* longer transit to remote regions
* stock-outs that turn orders into backorders
* a capacity squeeze during the November/December peak
* late deliveries lower the chance a customer orders again

Realistic data-quality problems are injected into the raw files on purpose
(duplicates, inconsistent labels, mixed date formats, impossible timestamps,
missing values) so the cleaning step has real work to do.
"""
import heapq

import numpy as np
import pandas as pd

from config import (CARRIER_SWITCH_DATE, WARMUP_START, DISPATCH_CUTOFF_HOUR, END_DATE,
                    PROMISE_WORKING_DAYS, RAW, REMOTE_REGIONS, SEED, START_DATE)

rng = np.random.default_rng(SEED)

UK_BANK_HOLIDAYS = [
    "2023-01-02", "2023-04-07", "2023-04-10", "2023-05-01", "2023-05-08", "2023-05-29",
    "2023-08-28", "2023-12-25", "2023-12-26",
    "2024-01-01", "2024-03-29", "2024-04-01", "2024-05-06", "2024-05-27", "2024-08-26",
    "2024-12-25", "2024-12-26",
    "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-05", "2025-05-26", "2025-08-25",
    "2025-12-25", "2025-12-26",
]
HOLIDAYS = np.array(UK_BANK_HOLIDAYS, dtype="datetime64[D]")
CARRIER_WEEKMASK = "1111110"  # carriers deliver Mon-Sat

REGIONS = {
    "London": 0.17, "South East": 0.15, "Midlands": 0.15, "North West": 0.12,
    "Yorkshire": 0.09, "South West": 0.09, "North East": 0.06,
    "Scotland": 0.09, "Wales": 0.05, "Northern Ireland": 0.03,
}
CHANNELS = {"Web": 0.55, "App": 0.30, "Marketplace": 0.15}
CATEGORIES = {  # name: (n_skus, price_low, price_high, stockout_risk)
    "Kitchen": (14, 8, 60, 0.020),
    "Bedding": (12, 15, 90, 0.025),
    "Lighting": (10, 12, 120, 0.030),
    "Decor": (14, 5, 45, 0.015),
    "Furniture": (10, 60, 400, 0.060),
}
# transit working days (Mon-Sat): probabilities for 1,2,3,4,5 days
TRANSIT = {
    "SwiftParcel": [0.80, 0.16, 0.03, 0.01, 0.00],
    "ParcelLink": [0.62, 0.28, 0.07, 0.02, 0.01],
    "BudgetPost": [0.38, 0.34, 0.17, 0.08, 0.03],
}
REMOTE_EXTRA_DAY_P = {"SwiftParcel": 0.15, "ParcelLink": 0.35, "BudgetPost": 0.60}


def build_products() -> pd.DataFrame:
    rows, i = [], 1
    for cat, (n, lo, hi, risk) in CATEGORIES.items():
        for _ in range(n):
            price = round(float(rng.uniform(lo, hi)), 2)
            rows.append({
                "sku": f"SKU{i:03d}", "category": cat,
                "product_name": f"{cat} item {i:03d}",
                "unit_price": price,
                "unit_cost": round(price * rng.uniform(0.52, 0.66), 2),
                "popularity": rng.gamma(2.0, 1.0),
                "stockout_risk": risk * rng.uniform(0.5, 1.8),
            })
            i += 1
    return pd.DataFrame(rows)


def daily_new_customers(dates: pd.DatetimeIndex) -> np.ndarray:
    t = np.arange(len(dates)) / 365.0
    trend = 1 + 0.12 * t
    dow = np.array([1.15, 1.05, 1.0, 1.0, 0.95, 0.85, 1.0])[dates.dayofweek]
    doy = dates.dayofyear.values
    season = 1 + 0.10 * np.cos(2 * np.pi * (doy - 15) / 365)
    peak = np.where((dates.month == 11) & (dates.day >= 15), 1.9, 1.0)
    peak = np.where((dates.month == 12) & (dates.day <= 18), 1.7, peak)
    peak = np.where((dates.month == 1) & (dates.day <= 20), 0.8, peak)
    black_friday = np.isin(dates.strftime("%m-%d"), ["11-24", "11-29", "11-28"])
    peak = np.where(black_friday, 3.0, peak)
    lam = 22 * trend * dow * season * peak
    return rng.poisson(lam)


def choose_carrier(order_date: pd.Timestamp) -> str:
    if order_date < pd.Timestamp(CARRIER_SWITCH_DATE):
        return rng.choice(["SwiftParcel", "ParcelLink"], p=[0.70, 0.30])
    return rng.choice(["BudgetPost", "SwiftParcel", "ParcelLink"], p=[0.65, 0.20, 0.15])


def dispatch_date(ts: pd.Timestamp) -> np.datetime64:
    d = np.datetime64(ts.date(), "D")
    if np.is_busday(d, holidays=HOLIDAYS):
        if ts.hour < DISPATCH_CUTOFF_HOUR:
            return d
        return np.busday_offset(d, 1, roll="forward", holidays=HOLIDAYS)
    return np.busday_offset(d, 0, roll="forward", holidays=HOLIDAYS)


def simulate():
    products = build_products()
    sku_p = (products["popularity"] / products["popularity"].sum()).values
    dates = pd.date_range(WARMUP_START, END_DATE, freq="D")  # warm-up builds a returning-customer base
    end = pd.Timestamp(END_DATE) + pd.Timedelta(days=1)
    new_counts = daily_new_customers(dates)
    region_names, region_p = list(REGIONS), np.array(list(REGIONS.values()))
    hour_p = np.array([1, 0.5, 0.3, 0.2, 0.2, 0.4, 1, 2, 3, 4, 4.5, 5, 5.5, 5, 4.5,
                       4.5, 4.5, 5, 6, 7, 7.5, 6.5, 4.5, 2.5])
    hour_p = hour_p / hour_p.sum()

    # event queue of (timestamp, customer_id) for every order
    queue = []
    customers = []
    cid = 0
    for d, n in zip(dates, new_counts):
        for _ in range(n):
            cid += 1
            ts = d + pd.Timedelta(hours=int(rng.choice(24, p=hour_p)),
                                  minutes=int(rng.integers(60)))
            customers.append({"customer_id": f"C{cid:06d}",
                              "region": rng.choice(region_names, p=region_p),
                              "signup_date": d.date(),
                              "preferred_channel": rng.choice(list(CHANNELS), p=list(CHANNELS.values()))})
            heapq.heappush(queue, (ts, cid))
    cust = pd.DataFrame(customers).set_index("customer_id")

    orders, lines = [], []
    oid = 0
    while queue:
        ts, c = heapq.heappop(queue)
        if ts >= end:
            continue
        oid += 1
        cust_id = f"C{c:06d}"
        region = cust.at[cust_id, "region"]
        channel = cust.at[cust_id, "preferred_channel"] if rng.random() < 0.8 else rng.choice(list(CHANNELS))
        carrier = choose_carrier(ts)
        n_items = int(min(1 + rng.poisson(0.7), 6))
        skus = rng.choice(len(products), size=n_items, p=sku_p, replace=False)
        is_peak = (ts.month == 11 and ts.day >= 15) or (ts.month == 12 and ts.day <= 20)

        value, stockout = 0.0, False
        for s in skus:
            qty = int(1 + rng.poisson(0.25))
            p = products.iloc[s]
            value += qty * p["unit_price"]
            risk = p["stockout_risk"] * (2.2 if is_peak else 1.0)
            line_stockout = rng.random() < risk
            stockout |= line_stockout
            lines.append({"order_id": f"O{oid:07d}", "sku": p["sku"], "quantity": qty,
                          "unit_price": p["unit_price"], "backordered": int(line_stockout)})

        disp = dispatch_date(ts)
        if stockout:
            disp = np.busday_offset(disp, int(rng.integers(2, 5)), holidays=HOLIDAYS)
        if is_peak and rng.random() < 0.30:
            disp = np.busday_offset(disp, 1, holidays=HOLIDAYS)
        transit = int(rng.choice([1, 2, 3, 4, 5], p=TRANSIT[carrier]))
        if region in REMOTE_REGIONS and rng.random() < REMOTE_EXTRA_DAY_P[carrier]:
            transit += 1
        delivered = np.busday_offset(disp, transit, weekmask=CARRIER_WEEKMASK, holidays=HOLIDAYS)
        order_day = np.busday_offset(np.datetime64(ts.date(), "D"), 0, roll="forward", holidays=HOLIDAYS)
        promised = np.busday_offset(order_day, PROMISE_WORKING_DAYS, holidays=HOLIDAYS)
        late = delivered > promised

        status = "Delivered"
        r = rng.random()
        if r < 0.015:
            status = "Cancelled"
        elif r < 0.015 + (0.07 if late else 0.035):
            status = "Returned"

        disp_ts = pd.Timestamp(disp) + pd.Timedelta(hours=int(rng.integers(15, 20)), minutes=int(rng.integers(60)))
        deliv_ts = pd.Timestamp(delivered) + pd.Timedelta(hours=int(rng.integers(8, 20)), minutes=int(rng.integers(60)))
        orders.append({
            "order_id": f"O{oid:07d}", "customer_id": cust_id,
            "order_datetime": ts, "channel": channel, "region": region,
            "carrier": carrier, "n_items": n_items,
            "order_value": round(value, 2),
            "shipping_fee": 0.0 if value >= 50 else 3.95,
            "promised_date": pd.Timestamp(promised).date(),
            "dispatch_datetime": None if status == "Cancelled" else disp_ts,
            "delivered_datetime": None if status == "Cancelled" else deliv_ts,
            "status": status,
        })

        # does the customer come back? Late delivery hurts retention.
        if status != "Cancelled":
            p_return = 0.30 if late else 0.42
            if rng.random() < p_return:
                gap = pd.Timedelta(days=float(rng.gamma(2.0, 30.0)) + 5)
                nxt = ts + gap
                nxt = nxt.normalize() + pd.Timedelta(hours=int(rng.choice(24, p=hour_p)),
                                                     minutes=int(rng.integers(60)))
                heapq.heappush(queue, (nxt, c))

    orders = pd.DataFrame(orders)
    lines = pd.DataFrame(lines)
    # keep only the analysis window; the warm-up months just seed repeat customers
    orders = orders[orders["order_datetime"] >= START_DATE].reset_index(drop=True)
    lines = lines[lines["order_id"].isin(orders["order_id"])].reset_index(drop=True)
    customers = cust.reset_index()
    return products, customers, orders, lines


def build_inventory(products: pd.DataFrame, lines: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """Stock on hand at year end: buyers over-ordered for peak, so cover is bloated."""
    o = orders[["order_id", "order_datetime"]]
    recent = lines.merge(o, on="order_id")
    recent = recent[recent["order_datetime"] >= "2025-07-01"]
    weekly = recent.groupby("sku")["quantity"].sum() / 26
    inv = products[["sku"]].copy()
    inv["avg_weekly_units_h2_2025"] = inv["sku"].map(weekly).fillna(0.5)
    cover_weeks = rng.uniform(5, 16, len(inv))
    inv["stock_on_hand"] = np.ceil(inv["avg_weekly_units_h2_2025"] * cover_weeks).astype(int)
    inv["snapshot_date"] = END_DATE
    return inv[["snapshot_date", "sku", "stock_on_hand"]]


def inject_quality_issues(customers, orders, lines):
    o = orders.copy()
    n = len(o)
    # 1. duplicated rows from a double export
    dup = o.sample(frac=0.008, random_state=SEED)
    o = pd.concat([o, dup], ignore_index=True)
    # 2. inconsistent region and carrier labels
    region_variants = {"London": ["london", "LONDON ", "Greater London"],
                       "Scotland": ["scotland", "SCOTLAND"],
                       "North West": ["North-West", "NW"],
                       "Northern Ireland": ["N. Ireland", "NI"]}
    for canon, variants in region_variants.items():
        idx = o.index[(o["region"] == canon) & (rng.random(len(o)) < 0.06)]
        o.loc[idx, "region"] = rng.choice(variants, len(idx))
    carrier_variants = {"BudgetPost": ["Budget Post", "budgetpost", "BUDGETPOST"],
                        "SwiftParcel": ["Swift Parcel", "swiftparcel"],
                        "ParcelLink": ["Parcel Link", "parcellink"]}
    for canon, variants in carrier_variants.items():
        idx = o.index[(o["carrier"] == canon) & (rng.random(len(o)) < 0.05)]
        o.loc[idx, "carrier"] = rng.choice(variants, len(idx))
    # 3. timestamps exported as strings; ~5% in UK dd/mm/yyyy format
    for col in ["order_datetime", "dispatch_datetime", "delivered_datetime"]:
        s = pd.to_datetime(o[col])
        iso = s.dt.strftime("%Y-%m-%d %H:%M:%S")
        uk = s.dt.strftime("%d/%m/%Y %H:%M")
        use_uk = rng.random(len(o)) < 0.05
        o[col] = np.where(s.isna(), None, np.where(use_uk, uk, iso))
    # 4. missing delivery scans on delivered orders
    idx = o.index[(o["status"] == "Delivered") & (rng.random(len(o)) < 0.015)]
    o.loc[idx, "delivered_datetime"] = None
    # 5. scanner clock errors: delivered before dispatched
    idx = o.index[(o["status"] == "Delivered") & o["delivered_datetime"].notna() & (rng.random(len(o)) < 0.003)]
    o.loc[idx, "delivered_datetime"] = "2020-01-01 00:00:00"
    # 6. bad order values
    idx = rng.choice(n, 25, replace=False)
    o.loc[idx, "order_value"] = rng.choice([0.0, -1.0, np.nan], 25)
    # 7. status casing
    idx = o.index[rng.random(len(o)) < 0.03]
    o.loc[idx, "status"] = o.loc[idx, "status"].str.upper()
    o = o.sample(frac=1, random_state=SEED).reset_index(drop=True)

    c = customers.copy()
    idx = c.index[rng.random(len(c)) < 0.01]
    c.loc[idx, "region"] = None
    return c, o, lines


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    products, customers, orders, lines = simulate()
    inventory = build_inventory(products, lines, orders)
    customers, orders_raw, lines = inject_quality_issues(customers, orders, lines)
    products.drop(columns=["popularity", "stockout_risk"]).to_csv(RAW / "products.csv", index=False)
    customers.to_csv(RAW / "customers.csv", index=False)
    orders_raw.to_csv(RAW / "orders.csv", index=False)
    lines.to_csv(RAW / "order_lines.csv", index=False)
    inventory.to_csv(RAW / "inventory_snapshot.csv", index=False)
    print(f"orders: {len(orders_raw):,} raw rows | lines: {len(lines):,} | customers: {len(customers):,}")


if __name__ == "__main__":
    main()
