"""Which factors drive late delivery? Logistic regression on post-switch orders.

A logistic regression is used on purpose: the client needs to know *how much*
each lever matters (odds ratios with confidence intervals), not just a score.
The fitted model is reused by opportunity_sizing.py to predict what happens to
lateness under each proposed change (counterfactual scoring).
"""
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from sklearn.metrics import roc_auc_score

from config import CARRIER_SWITCH_DATE, PROCESSED, REPORTS

FORMULA = ("is_late ~ C(carrier, Treatment('SwiftParcel')) * is_remote + after_cutoff"
           " + fri_or_weekend + is_peak + has_backorder + multi_item + C(channel, Treatment('Web'))")

LABELS = {
    "C(carrier, Treatment('SwiftParcel'))[T.BudgetPost]": "Carrier: BudgetPost (vs SwiftParcel)",
    "C(carrier, Treatment('SwiftParcel'))[T.ParcelLink]": "Carrier: ParcelLink (vs SwiftParcel)",
    "is_remote": "Remote region (Scotland, Wales, NI)",
    "C(carrier, Treatment('SwiftParcel'))[T.BudgetPost]:is_remote": "BudgetPost x remote (extra)",
    "C(carrier, Treatment('SwiftParcel'))[T.ParcelLink]:is_remote": "ParcelLink x remote (extra)",
    "after_cutoff": "Ordered after 14:00 cut-off",
    "fri_or_weekend": "Ordered Fri-Sun",
    "is_peak": "Peak season (mid-Nov to Dec 20)",
    "has_backorder": "Item out of stock (backorder)",
    "multi_item": "Multi-item order",
    "C(channel, Treatment('Web'))[T.App]": "Channel: App (vs Web)",
    "C(channel, Treatment('Web'))[T.Marketplace]": "Channel: Marketplace (vs Web)",
}


def load_model_data() -> pd.DataFrame:
    o = pd.read_csv(PROCESSED / "orders_clean.csv", parse_dates=["order_datetime"])
    o = o[o["is_late"].notna() & (o["order_datetime"] >= CARRIER_SWITCH_DATE)].copy()
    o["fri_or_weekend"] = o["order_dow"].isin(["Friday", "Saturday", "Sunday"]).astype(int)
    o["multi_item"] = (o["n_items"] > 1).astype(int)
    o["is_late"] = o["is_late"].astype(int)
    return o


def fit(df: pd.DataFrame):
    return smf.logit(FORMULA, data=df).fit(disp=False)


def main():
    df = load_model_data()
    train = df[df["order_datetime"] < "2025-07-01"]
    test = df[df["order_datetime"] >= "2025-07-01"]
    m_train = fit(train)
    auc = roc_auc_score(test["is_late"], m_train.predict(test))

    model = fit(df)  # refit on all post-switch data for interpretation
    ci = model.conf_int()
    table = pd.DataFrame({
        "driver": [LABELS.get(k, k) for k in model.params.index],
        "odds_ratio": np.exp(model.params.values),
        "ci_low": np.exp(ci[0].values), "ci_high": np.exp(ci[1].values),
        "p_value": model.pvalues.values,
    }).iloc[1:]  # drop intercept
    table = table.sort_values("odds_ratio", ascending=False)
    table.to_csv(REPORTS / "late_delivery_drivers.csv", index=False)
    pd.DataFrame([{"holdout_auc": auc, "n_train": len(train), "n_test": len(test),
                   "pseudo_r2": model.prsquared}]).to_csv(REPORTS / "model_metrics.csv", index=False)
    print(table.round(3).to_string(index=False))
    print(f"\nHold-out AUC (trained Jul-24..Jun-25, tested Jul-25..Dec-25): {auc:.3f}")


if __name__ == "__main__":
    main()
