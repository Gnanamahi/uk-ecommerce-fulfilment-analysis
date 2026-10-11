"""Put a £ value on each fix, then stress-test it with Monte Carlo simulation.

Method
1. Cost of one late delivery = support contacts + goodwill vouchers + extra
   returns + lost future margin (late customers come back less often).
2. Levers A-D are scored with the fitted driver model: change the feature
   (e.g. carrier) for the affected 2025 orders and predict the new late count.
3. Lever E (Saturday dispatch) is not a feature in the model, so it is scored by
   replaying 2025 orders through the dispatch rules with a Saturday shift.
4. Monte Carlo: draw model coefficients from their sampling distribution and
   business assumptions from plausible ranges -> P10 / P50 / P90 net benefit.
"""
import numpy as np
import pandas as pd
from patsy import build_design_matrices

from config import (CARRIER_RATES, CS_CONTACT_COST, CS_CONTACT_RATE, GOODWILL_COST,
                    GOODWILL_RATE, GROSS_MARGIN, HOLDING_COST_RATE, INVENTORY_LEAD_TIME_WEEKS,
                    INVENTORY_REVIEW_WEEKS, LATE_SHIFT_COST, PROCESSED, REPORTS, SEED,
                    SERVICE_LEVEL_Z, WEEKEND_SHIFT_COST)
from drivers_model import fit, load_model_data
from generate_data import CARRIER_WEEKMASK, HOLIDAYS

RETURN_HANDLING_COST = 8.00      # £ per return (label + handling)
RETURN_WRITE_OFF = 0.15          # share of returned value that cannot be resold
BACKORDER_REDUCTION = 0.50       # expected cut in backorders from forecast-led replenishment
REPLENISHMENT_RUN_COST = 5_000   # £/yr analyst time to run the weekly re-order process
N_SIMS = 5_000


# ---------------------------------------------------------------- cost of late
def cost_per_late_order(orders: pd.DataFrame) -> dict:
    o = orders[orders["status"] != "Cancelled"].sort_values(["customer_id", "order_datetime"])
    o["order_rank"] = o.groupby("customer_id").cumcount() + 1
    n_orders = o.groupby("customer_id")["order_id"].transform("count")
    first = o[(o["order_rank"] == 1) & o["is_late"].notna() & (o["order_datetime"] < "2025-07-01")]
    repeat_on_time = (n_orders[first.index][first["is_late"] == 0] > 1).mean()
    repeat_late = (n_orders[first.index][first["is_late"] == 1] > 1).mean()
    repeat_gap = repeat_on_time - repeat_late

    aov = o["order_value"].mean()
    repeaters = o.groupby("customer_id")["order_id"].count()
    future_orders = (repeaters[repeaters > 1] - 1).mean()
    future_margin = future_orders * aov * GROSS_MARGIN

    y25 = o[(o["order_datetime"].dt.year == 2025) & o["is_late"].notna()]
    ret = y25.assign(ret=(y25["status"] == "Returned").astype(int)).groupby("is_late")["ret"].mean()
    return_gap = ret.loc[1.0] - ret.loc[0.0]

    parts = {
        "support_contacts": CS_CONTACT_RATE * CS_CONTACT_COST,
        "goodwill_vouchers": GOODWILL_RATE * GOODWILL_COST,
        "extra_returns": return_gap * (RETURN_HANDLING_COST + RETURN_WRITE_OFF * aov),
        "lost_future_margin": repeat_gap * future_margin,
    }
    parts["total"] = sum(parts.values())
    meta = {"repeat_on_time": repeat_on_time, "repeat_late": repeat_late, "repeat_gap": repeat_gap,
            "future_margin_per_returner": future_margin, "return_gap": return_gap, "aov": aov}
    return parts, meta


# ------------------------------------------------------------------ inventory
def inventory_targets() -> pd.DataFrame:
    lines = pd.read_csv(PROCESSED / "order_lines_clean.csv")
    orders = pd.read_csv(PROCESSED / "orders_clean.csv", usecols=["order_id", "order_date", "status"],
                         parse_dates=["order_date"])
    orders = orders[orders["status"] != "Cancelled"]
    products = pd.read_csv(PROCESSED / "products_clean.csv")
    inv = pd.read_csv(PROCESSED / "inventory_clean.csv")
    fc = pd.read_csv(REPORTS / "forecast_q1_2026.csv")

    recent = lines.merge(orders, on="order_id")
    recent = recent[recent["order_date"] >= "2025-07-01"]
    wk = recent.groupby(["sku", pd.Grouper(key="order_date", freq="W-SUN")])["quantity"].sum().unstack(fill_value=0)
    share = wk.sum(axis=1) / wk.values.sum()
    sd = wk.std(axis=1)

    weekly_fc = fc["forecast_units"].mean()
    t = products[["sku", "category", "unit_cost"]].merge(inv, on="sku")
    t["weekly_forecast"] = t["sku"].map(share).fillna(0) * weekly_fc
    t["weekly_sd"] = t["sku"].map(sd).fillna(0)
    t["safety_stock"] = SERVICE_LEVEL_Z * t["weekly_sd"] * np.sqrt(INVENTORY_LEAD_TIME_WEEKS)
    t["target_stock"] = np.ceil(t["weekly_forecast"] * (INVENTORY_LEAD_TIME_WEEKS + INVENTORY_REVIEW_WEEKS)
                                + t["safety_stock"])
    t["weeks_cover_now"] = t["stock_on_hand"] / t["weekly_forecast"].replace(0, np.nan)
    t["excess_units"] = (t["stock_on_hand"] - t["target_stock"]).clip(lower=0)
    t["excess_value_gbp"] = t["excess_units"] * t["unit_cost"]
    t.to_csv(REPORTS / "inventory_targets.csv", index=False)
    return t


# ------------------------------------------------------------ lever scenarios
LEVER_NAMES = {
    "A": "Route remote regions (Scotland, Wales, NI) from BudgetPost to SwiftParcel",
    "B": "Move remaining (non-remote) BudgetPost volume to SwiftParcel",
    "C": "Move dispatch cut-off from 14:00 to 16:00",
    "D": "Forecast-led replenishment: fewer stock-outs + release excess stock",
    "E": "Add a Saturday dispatch shift",
}


def scenarios(df: pd.DataFrame) -> dict:
    """Return {lever: mask of affected orders, modified dataframe} for model-scored levers."""
    bp = df["carrier"] == "BudgetPost"
    masks = {"A": bp & (df["is_remote"] == 1), "B": bp & (df["is_remote"] == 0)}
    weekday = ~df["order_dow"].isin(["Saturday", "Sunday"])
    masks["C"] = weekday & df["order_hour"].between(14, 15) & (df["has_backorder"] == 0)
    out = {}
    for k in ["A", "B"]:
        f = df.copy(); f.loc[masks[k], "carrier"] = "SwiftParcel"; out[k] = (f, masks[k])
    f = df.copy(); f.loc[masks["C"], "after_cutoff"] = 0; out["C"] = (f, masks["C"])
    return out


def apply_plan(df: pd.DataFrame, sc: dict, keep: list) -> pd.DataFrame:
    combo = df.copy()
    for k in ["A", "B"]:
        if k in keep:
            combo.loc[sc[k][1], "carrier"] = "SwiftParcel"
    if "C" in keep:
        combo.loc[sc["C"][1], "after_cutoff"] = 0
    return combo


def replay_saturday_shift(df: pd.DataFrame) -> pd.DataFrame:
    """Re-run dispatch for 2025 orders if the warehouse also dispatched on Saturdays."""
    d = df[df["has_backorder"] == 0].copy()
    order_day = d["order_datetime"].dt.normalize().values.astype("datetime64[D]")
    dispatched = pd.to_datetime(d["dispatch_datetime"]).dt.normalize().values.astype("datetime64[D]")
    delivered = pd.to_datetime(d["delivered_date"]).values.astype("datetime64[D]")
    promised = pd.to_datetime(d["promised_date"]).values.astype("datetime64[D]")
    transit = np.busday_count(dispatched, delivered, weekmask=CARRIER_WEEKMASK, holidays=HOLIDAYS)

    dow = d["order_datetime"].dt.dayofweek.values
    hour = d["order_hour"].values
    fri_late = (dow == 4) & (hour >= 14)
    sat_early = (dow == 5) & (hour < 14)
    new_dispatch = dispatched.copy()
    new_dispatch[fri_late] = order_day[fri_late] + np.timedelta64(1, "D")
    new_dispatch[sat_early] = order_day[sat_early]
    moved = (fri_late | sat_early) & (new_dispatch < dispatched)
    new_deliv = np.busday_offset(new_dispatch, transit, weekmask=CARRIER_WEEKMASK, holidays=HOLIDAYS)
    d["late_new"] = np.where(moved, new_deliv > promised, d["is_late"].astype(bool))
    d["moved"] = moved
    return d


def main():
    rng = np.random.default_rng(SEED)
    orders = pd.read_csv(PROCESSED / "orders_clean.csv",
                         parse_dates=["order_datetime", "promised_date", "delivered_date"])
    late_cost, meta = cost_per_late_order(orders)
    pd.DataFrame([late_cost]).to_csv(REPORTS / "cost_per_late_order.csv", index=False)
    pd.DataFrame([meta]).to_csv(REPORTS / "late_cost_inputs.csv", index=False)

    df_all = load_model_data()
    model = fit(df_all)
    df = df_all[df_all["order_datetime"].dt.year == 2025].reset_index(drop=True)
    design_info = model.model.data.orig_exog.design_info
    X = lambda frame: np.asarray(build_design_matrices([design_info], frame)[0])
    sig = lambda z: 1 / (1 + np.exp(-z))

    beta = model.params.values
    base_late_actual = df["is_late"].sum()
    sc = scenarios(df)
    no_bo = df.copy(); no_bo["has_backorder"] = 0
    Xd, Xnb = X(df), X(no_bo)
    Xs = {k: X(sc[k][0]) for k in ["A", "B", "C"]}
    bo_mask = (df["has_backorder"] == 1).values

    def late_reduction(b, bo_cut=BACKORDER_REDUCTION):
        base = sig(Xd @ b)
        red = {k: base.sum() - sig(Xs[k] @ b).sum() for k in ["A", "B", "C"]}
        # replenishment: a share of backordered orders behave like in-stock orders
        red["D"] = bo_cut * (base - sig(Xnb @ b))[bo_mask].sum()
        return red

    red = late_reduction(beta)
    inv = inventory_targets()
    excess_value = inv["excess_value_gbp"].sum()
    sat = replay_saturday_shift(df)
    red["E"] = sat["is_late"].sum() - sat["late_new"].sum()

    rate_gap = CARRIER_RATES["SwiftParcel"] - CARRIER_RATES["BudgetPost"]
    costs = {"A": sc["A"][1].sum() * rate_gap, "B": sc["B"][1].sum() * rate_gap,
             "C": LATE_SHIFT_COST, "D": REPLENISHMENT_RUN_COST, "E": WEEKEND_SHIFT_COST}
    extra = {k: 0.0 for k in "ABCDE"}
    extra["D"] = excess_value * HOLDING_COST_RATE
    rows = []
    for k in "ABCDE":
        benefit = red[k] * late_cost["total"] + extra[k]
        rows.append({"lever": k, "description": LEVER_NAMES[k], "late_orders_avoided": red[k],
                     "late_cost_avoided_gbp": red[k] * late_cost["total"],
                     "other_benefit_gbp": extra[k], "annual_cost_gbp": costs[k],
                     "net_benefit_gbp": benefit - costs[k],
                     "roi_pct": 100 * (benefit - costs[k]) / costs[k]})
    levers = pd.DataFrame(rows)

    # ---- combined plan: all levers with positive net benefit, scored jointly
    keep = levers.loc[levers["net_benefit_gbp"] > 0, "lever"].tolist()
    combo = apply_plan(df, sc, keep)
    p_base = sig(Xd @ beta)
    p_combo = sig(X(combo) @ beta)
    if "D" in keep:
        combo_nb = combo.copy(); combo_nb["has_backorder"] = 0
        p_nb = sig(X(combo_nb) @ beta)
        p_combo[bo_mask] -= BACKORDER_REDUCTION * (p_combo[bo_mask] - p_nb[bo_mask])
    # calibrate to actual: apply the model's relative change to the observed late count
    combo_late = base_late_actual * p_combo.sum() / p_base.sum()
    otd_now = 1 - base_late_actual / len(df)
    otd_plan = 1 - combo_late / len(df)

    # ---- Monte Carlo
    draws = rng.multivariate_normal(beta, model.cov_params().values, size=N_SIMS)
    sims = []
    for i in range(N_SIMS):
        cost_late = (rng.uniform(0.25, 0.45) * rng.uniform(3.5, 6.0) + rng.uniform(0.15, 0.25) * GOODWILL_COST
                     + late_cost["extra_returns"] * rng.uniform(0.6, 1.4)
                     + late_cost["lost_future_margin"] * rng.uniform(0.5, 1.5))
        r = late_reduction(draws[i], rng.uniform(0.30, 0.70))
        net = {"A": r["A"] * cost_late - costs["A"],
               "B": r["B"] * cost_late - costs["B"],
               "C": r["C"] * cost_late - costs["C"] * rng.uniform(0.9, 1.2),
               "D": r["D"] * cost_late + excess_value * rng.uniform(0.15, 0.25) - costs["D"] * rng.uniform(0.8, 1.5),
               "E": red["E"] * cost_late - costs["E"] * rng.uniform(0.9, 1.2)}
        net["plan"] = sum(net[k] for k in keep)
        sims.append(net)
    sims = pd.DataFrame(sims)
    sims.to_csv(REPORTS / "monte_carlo_draws.csv", index=False)
    q = sims.quantile([0.1, 0.5, 0.9]).T
    q.columns = ["P10", "P50", "P90"]
    q["prob_positive"] = (sims > 0).mean()
    q.index.name = "lever"
    q.to_csv(REPORTS / "monte_carlo_summary.csv")

    levers.to_csv(REPORTS / "opportunity_levers.csv", index=False)
    bp = df["carrier"] == "BudgetPost"
    summary = {
        "otd_2025_pct": 100 * otd_now, "otd_plan_pct": 100 * otd_plan,
        "late_orders_2025": int(base_late_actual), "late_orders_plan": combo_late,
        "cost_per_late_order_gbp": late_cost["total"],
        "cost_of_late_2025_gbp": base_late_actual * late_cost["total"],
        "budgetpost_postage_saving_gbp": bp.sum() * rate_gap,
        "budgetpost_extra_late_cost_gbp": (red["A"] + red["B"]) * late_cost["total"],
        "recommended_levers": "+".join(keep),
        "plan_net_benefit_gbp": levers.loc[levers["lever"].isin(keep), "net_benefit_gbp"].sum(),
        "excess_stock_released_gbp": excess_value,
        "saturday_shift_net_gbp": levers.loc[levers["lever"] == "E", "net_benefit_gbp"].iloc[0],
    }
    pd.DataFrame([summary]).to_csv(REPORTS / "headline_summary.csv", index=False)

    pd.set_option("display.width", 200)
    print("Cost per late order:", {k: round(float(v), 2) for k, v in late_cost.items()})
    print(levers.round(0).to_string(index=False))
    print(q.round(0).to_string())
    print({k: (round(float(v), 1) if not isinstance(v, str) else v) for k, v in summary.items()})


if __name__ == "__main__":
    main()
