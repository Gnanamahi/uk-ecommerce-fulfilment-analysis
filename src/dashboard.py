"""Build the interactive executive dashboard (reports/dashboard.html) and
static PNG charts for the README (reports/figures/).

Open reports/dashboard.html in any browser - every chart has hover tooltips.
PNG export needs Kaleido + Chrome; if that is missing the HTML is still built.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import CARRIER_SWITCH_DATE, FIGURES, PROCESSED, REPORTS

# Validated categorical palette (fixed order) + text / surface tokens
CARRIER_COLOURS = {"SwiftParcel": "#2a78d6", "ParcelLink": "#eb6834", "BudgetPost": "#1baf7a"}
POSITIVE, NEGATIVE, NEUTRAL = "#2a78d6", "#e34948", "#8c8b86"
TEXT, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e7e6e2", "#fcfcfb"
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
FONT = "Inter, Segoe UI, Helvetica, Arial, sans-serif"


def style(fig: go.Figure, title: str, height=420) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=16, color=TEXT)),
        font=dict(family=FONT, size=12, color=MUTED), height=height,
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, margin=dict(l=60, r=30, t=60, b=50),
        hoverlabel=dict(bgcolor="white", font=dict(color=TEXT, family=FONT)),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, title=None),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def otd_trend() -> go.Figure:
    o = pd.read_csv(PROCESSED / "orders_clean.csv", usecols=["order_date", "carrier", "is_late"],
                    parse_dates=["order_date"]).dropna(subset=["is_late"])
    o["month"] = o["order_date"].dt.to_period("M").dt.to_timestamp()
    fig = go.Figure()
    total = o.groupby("month")["is_late"].mean().mul(-100).add(100)
    fig.add_scatter(x=total.index, y=total.values, name="All orders", mode="lines",
                    line=dict(color=TEXT, width=2, dash="dot"),
                    hovertemplate="%{x|%b %Y}<br>All orders: %{y:.1f}%<extra></extra>")
    for c, col in CARRIER_COLOURS.items():
        s = o[o["carrier"] == c].groupby("month")["is_late"].agg(["mean", "size"])
        s = s[s["size"] >= 50]
        fig.add_scatter(x=s.index, y=100 - 100 * s["mean"], name=c, mode="lines",
                        line=dict(color=col, width=2),
                        customdata=s["size"],
                        hovertemplate=f"%{{x|%b %Y}}<br>{c}: %{{y:.1f}}%<br>%{{customdata:,}} orders<extra></extra>")
    fig.add_vline(x=pd.Timestamp(CARRIER_SWITCH_DATE), line=dict(color=MUTED, width=1, dash="dash"))
    fig.add_annotation(x=pd.Timestamp(CARRIER_SWITCH_DATE), y=1, yref="paper", showarrow=False,
                       text="BudgetPost introduced", xanchor="left", xshift=6, font=dict(color=MUTED))
    fig.update_yaxes(title="On-time delivery %", ticksuffix="%", range=[55, 100])
    return style(fig, "On-time delivery fell after the switch to BudgetPost, and every peak season is worse")


def drivers() -> go.Figure:
    d = pd.read_csv(REPORTS / "late_delivery_drivers.csv").sort_values("odds_ratio")
    sig = d["p_value"] < 0.05
    colours = np.where(~sig, NEUTRAL, np.where(d["odds_ratio"] > 1, NEGATIVE, POSITIVE))
    fig = go.Figure(go.Scatter(
        x=d["odds_ratio"], y=d["driver"], mode="markers",
        marker=dict(size=10, color=colours, line=dict(color=SURFACE, width=2)),
        error_x=dict(type="data", symmetric=False, array=d["ci_high"] - d["odds_ratio"],
                     arrayminus=d["odds_ratio"] - d["ci_low"], color=MUTED, thickness=1.5, width=0),
        customdata=np.stack([d["ci_low"], d["ci_high"], d["p_value"]], axis=1),
        hovertemplate="%{y}<br>Odds ratio %{x:.2f} (95% CI %{customdata[0]:.2f}-%{customdata[1]:.2f})"
                      "<br>p = %{customdata[2]:.3f}<extra></extra>"))
    fig.add_vline(x=1, line=dict(color=MUTED, width=1))
    fig.update_xaxes(type="log", tickvals=[0.5, 1, 2, 5, 10, 20, 50, 100, 200], ticktext=["0.5", "1", "2", "5", "10", "20", "50", "100", "200"], title="Odds ratio of a late delivery (log scale; 1 = no effect)")
    fig.update_yaxes(showgrid=False)
    return style(fig, "What makes an order late? Stock-outs, the carrier and the 14:00 cut-off dominate", 480)


def heatmap() -> go.Figure:
    q = pd.read_csv(REPORTS / "sql" / "q3_late_rate_carrier_region.csv")
    p = q.pivot(index="carrier", columns="region", values="late_pct")
    p = p[p.mean().sort_values(ascending=False).index].reindex(["BudgetPost", "ParcelLink", "SwiftParcel"])
    n = q.pivot(index="carrier", columns="region", values="orders").reindex(index=p.index, columns=p.columns)
    fig = go.Figure(go.Heatmap(
        z=p.values, x=p.columns, y=p.index, colorscale=[[i / 6, c] for i, c in enumerate(SEQ_BLUE)],
        xgap=2, ygap=2, text=p.round(0).astype(int).astype(str).values + "%", texttemplate="%{text}",
        customdata=n.values, colorbar=dict(title="Late %", ticksuffix="%"),
        hovertemplate="%{y} to %{x}<br>Late: %{z:.1f}%<br>%{customdata:,} orders<extra></extra>"))
    return style(fig, "BudgetPost is late on roughly 1 in 3 deliveries to Scotland, Wales and NI (2025)", 340)


def levers() -> go.Figure:
    lv = pd.read_csv(REPORTS / "opportunity_levers.csv")
    mc = pd.read_csv(REPORTS / "monte_carlo_summary.csv").set_index("lever")
    lv = lv.join(mc, on="lever")
    short = {"A": "A. Remote regions to SwiftParcel", "B": "B. Other BudgetPost to SwiftParcel",
             "C": "C. Cut-off 14:00 to 16:00", "D": "D. Forecast-led replenishment",
             "E": "E. Saturday dispatch shift"}
    lv["label"] = lv["lever"].map(short)
    colours = np.where(lv["P50"] > 0, POSITIVE, NEGATIVE)
    fig = go.Figure(go.Bar(
        x=lv["P50"], y=lv["label"], orientation="h", marker=dict(color=colours, cornerradius=4),
        error_x=dict(type="data", symmetric=False, array=lv["P90"] - lv["P50"],
                     arrayminus=lv["P50"] - lv["P10"], color=TEXT, thickness=1.5, width=6),
        customdata=np.stack([lv["P10"], lv["P90"], lv["late_orders_avoided"], lv["annual_cost_gbp"]], axis=1),
        hovertemplate="%{y}<br>Net benefit (P50): £%{x:,.0f}/yr<br>P10-P90: £%{customdata[0]:,.0f} to "
                      "£%{customdata[1]:,.0f}<br>Late orders avoided: %{customdata[2]:,.0f}"
                      "<br>Annual cost: £%{customdata[3]:,.0f}<extra></extra>"))
    fig.add_vline(x=0, line=dict(color=MUTED, width=1))
    fig.update_xaxes(title="Net annual benefit, £ (bar = P50, whisker = P10-P90 from 5,000 simulations)",
                     tickprefix="£", tickformat=",.0f", showgrid=True, gridcolor=GRID)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return style(fig, "Carrier and stock fixes pay back; a later cut-off and a Saturday shift lose money", 400)


def forecast() -> go.Figure:
    hist = pd.read_csv(REPORTS / "weekly_units.csv", parse_dates=["week"])
    bt = pd.read_csv(REPORTS / "forecast_backtest_weeks.csv", parse_dates=["week"])
    fc = pd.read_csv(REPORTS / "forecast_q1_2026.csv", parse_dates=["week"])
    scores = pd.read_csv(REPORTS / "forecast_backtest.csv").set_index("model")
    fig = go.Figure()
    fig.add_scatter(x=hist["week"], y=hist["units"], name="Actual units", mode="lines",
                    line=dict(color=TEXT, width=1.5),
                    hovertemplate="w/e %{x|%d %b %Y}<br>Actual: %{y:,.0f}<extra></extra>")
    fig.add_scatter(x=bt["week"], y=bt["seasonal_naive"],
                    name=f"Seasonal naive back-test (WAPE {scores.loc['Seasonal naive', 'WAPE_%']:.1f}%)",
                    mode="lines", line=dict(color=CARRIER_COLOURS["ParcelLink"], width=2, dash="dot"),
                    hovertemplate="w/e %{x|%d %b %Y}<br>Seasonal naive: %{y:,.0f}<extra></extra>")
    fig.add_scatter(x=bt["week"], y=bt["holt_winters"],
                    name=f"Holt-Winters back-test (WAPE {scores.loc['Holt-Winters', 'WAPE_%']:.1f}%)",
                    mode="lines", line=dict(color=CARRIER_COLOURS["SwiftParcel"], width=2),
                    hovertemplate="w/e %{x|%d %b %Y}<br>Holt-Winters: %{y:,.0f}<extra></extra>")
    fig.add_scatter(x=fc["week"], y=fc["forecast_units"], name="Q1 2026 forecast", mode="lines+markers",
                    line=dict(color=CARRIER_COLOURS["SwiftParcel"], width=2), marker=dict(size=8),
                    hovertemplate="w/e %{x|%d %b %Y}<br>Forecast: %{y:,.0f}<extra></extra>")
    fig.update_yaxes(title="Units per week", tickformat=",")
    return style(fig, "Weekly demand: Holt-Winters back-tested against same-week-last-year, then projected", 420)


def kpi_html() -> str:
    h = pd.read_csv(REPORTS / "headline_summary.csv").iloc[0]
    mc = pd.read_csv(REPORTS / "monte_carlo_summary.csv").set_index("lever")
    yr = pd.read_csv(REPORTS / "sql" / "q1_otd_by_year.csv").set_index("year")["otd_pct"]
    tiles = [
        ("On-time delivery 2025", f"{h['otd_2025_pct']:.1f}%", f"down from {yr.iloc[0]:.1f}% in {yr.index[0]}"),
        ("With recommended plan", f"{h['otd_plan_pct']:.1f}%", f"levers {h['recommended_levers']}"),
        ("Cost of late deliveries", f"£{h['cost_of_late_2025_gbp'] / 1000:,.0f}k", f"£{h['cost_per_late_order_gbp']:.2f} per late order"),
        ("Plan net benefit / yr", f"£{mc.loc['plan', 'P50'] / 1000:,.0f}k",
         f"P10 £{mc.loc['plan', 'P10'] / 1000:,.0f}k to P90 £{mc.loc['plan', 'P90'] / 1000:,.0f}k"),
        ("Excess stock released", f"£{h['excess_stock_released_gbp'] / 1000:,.0f}k", "one-off cash, at cost"),
    ]
    return "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
                   for k, v, s in tiles)


def main():
    figs = {"otd_trend": otd_trend(), "drivers": drivers(), "carrier_region_heatmap": heatmap(),
            "levers": levers(), "forecast": forecast()}
    body = "".join(f.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False})
                   for f in figs.values())
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Northbrook Home - Fulfilment Diagnostic</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 body{{margin:0;background:{SURFACE};color:{TEXT};font-family:{FONT}}}
 main{{max-width:1100px;margin:0 auto;padding:24px 16px}}
 h1{{font-size:24px;margin:0 0 4px}} p.sub{{color:{MUTED};margin:0 0 20px}}
 .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:16px}}
 .tile{{border:1px solid {GRID};border-radius:8px;padding:14px;background:white}}
 .k{{font-size:12px;color:{MUTED}}} .v{{font-size:28px;font-weight:600;margin:4px 0}} .s{{font-size:12px;color:{MUTED}}}
 .plotly-graph-div{{border:1px solid {GRID};border-radius:8px;margin-bottom:16px;background:{SURFACE}}}
 footer{{color:{MUTED};font-size:12px}}
</style></head><body><main>
<h1>Northbrook Home: Why are our deliveries late?</h1>
<p class="sub">Fulfilment diagnostic, Jan 2023 - Dec 2025. Simulated data for a fictional UK homeware retailer.</p>
<div class="tiles">{kpi_html()}</div>
{body}
<footer>Built with Python, SQLite, statsmodels and Plotly. Hover any chart for detail.</footer>
</main></body></html>"""
    (REPORTS / "dashboard.html").write_text(html, encoding="utf-8")
    print("wrote reports/dashboard.html")

    FIGURES.mkdir(parents=True, exist_ok=True)
    try:
        for name, f in figs.items():
            f.write_image(FIGURES / f"{name}.png", width=1100, height=f.layout.height, scale=2)
        print("wrote PNGs to reports/figures/")
    except Exception as e:  # Kaleido/Chrome not installed
        print(f"skipped PNG export ({type(e).__name__}); the HTML dashboard is complete")


if __name__ == "__main__":
    main()
