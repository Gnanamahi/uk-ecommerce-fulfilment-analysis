"""Weekly demand forecast: Holt-Winters vs a seasonal-naive benchmark.

The benchmark matters: a model is only worth using if it beats "same week last
year". The last 13 weeks of 2025 (including peak) are held out for testing,
then the chosen model is refit on all data to forecast Q1 2026, which feeds the
inventory target in opportunity_sizing.py.
"""
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from config import PROCESSED, REPORTS

HORIZON = 13
SEASON = 52


def weekly_units() -> pd.Series:
    o = pd.read_csv(PROCESSED / "orders_clean.csv", usecols=["order_id", "order_date", "status"],
                    parse_dates=["order_date"])
    o = o[o["status"] != "Cancelled"]
    lines = pd.read_csv(PROCESSED / "order_lines_clean.csv", usecols=["order_id", "quantity"])
    daily = lines.merge(o, on="order_id").groupby("order_date")["quantity"].sum()
    weekly = daily.resample("W-SUN").sum()
    last_full_week = daily.index.max() - pd.Timedelta(days=(daily.index.max().dayofweek + 1) % 7)
    # keep complete Mon-Sun weeks only (partial first/last weeks would look like demand crashes)
    return weekly[(weekly.index >= "2023-01-08") & (weekly.index <= last_full_week)]


def holt_winters(y: pd.Series):
    return ExponentialSmoothing(y, trend="add", damped_trend=True, seasonal="mul",
                                seasonal_periods=SEASON, initialization_method="estimated").fit()


def seasonal_naive(y: pd.Series, h: int) -> np.ndarray:
    return y.iloc[-SEASON:-SEASON + h].values if h < SEASON else y.iloc[-SEASON:].values


def metrics(actual, pred) -> dict:
    actual, pred = np.asarray(actual), np.asarray(pred)
    return {"MAE": np.mean(np.abs(actual - pred)),
            "MAPE_%": 100 * np.mean(np.abs(actual - pred) / actual),
            "WAPE_%": 100 * np.abs(actual - pred).sum() / actual.sum(),
            "bias_%": 100 * (pred.sum() - actual.sum()) / actual.sum()}


def main():
    y = weekly_units()
    train, test = y.iloc[:-HORIZON], y.iloc[-HORIZON:]

    hw_pred = holt_winters(train).forecast(HORIZON).values
    sn_pred = seasonal_naive(train, HORIZON)
    scores = pd.DataFrame({"Holt-Winters": metrics(test, hw_pred),
                           "Seasonal naive": metrics(test, sn_pred)}).T
    scores.index.name = "model"
    scores.to_csv(REPORTS / "forecast_backtest.csv")

    backtest = pd.DataFrame({"week": test.index, "actual": test.values,
                             "holt_winters": hw_pred, "seasonal_naive": sn_pred})
    backtest.to_csv(REPORTS / "forecast_backtest_weeks.csv", index=False)

    final = holt_winters(y)
    future_idx = pd.date_range(y.index[-1] + pd.Timedelta(weeks=1), periods=HORIZON, freq="W-SUN")
    fc = pd.DataFrame({"week": future_idx, "forecast_units": final.forecast(HORIZON).values})
    fc.to_csv(REPORTS / "forecast_q1_2026.csv", index=False)
    pd.DataFrame({"week": y.index, "units": y.values}).to_csv(REPORTS / "weekly_units.csv", index=False)

    print(scores.round(2).to_string())
    print(f"\nQ1 2026 forecast: {fc['forecast_units'].sum():,.0f} units over {HORIZON} weeks")


if __name__ == "__main__":
    main()
