# Rebuilding the dashboard in Power BI

The Python pipeline produces a star schema that loads straight into Power BI Desktop.

## 1. Get the data

Run `python run_all.py` first. Then load **either** source:

- **CSV (simplest):** *Get data → Text/CSV* and load the files in `data/processed/` (`orders_clean.csv`, `order_lines_clean.csv`, `customers_clean.csv`, `products_clean.csv`, `inventory_clean.csv`).
- **SQLite (star schema):** install the SQLite ODBC driver, then *Get data → ODBC* and point it at `data/northbrook.db`. Load every `dim_*` and `fact_*` table.

## 2. Model view (SQLite route)

Create one-to-many, single-direction relationships:

| From (many) | To (one) |
|---|---|
| `fact_orders[order_date_key]` | `dim_date[date_key]` |
| `fact_orders[carrier_key]` | `dim_carrier[carrier_key]` |
| `fact_orders[region_key]` | `dim_region[region_key]` |
| `fact_orders[customer_key]` | `dim_customer[customer_key]` |
| `fact_order_lines[order_id]` | `fact_orders[order_id]` |
| `fact_order_lines[product_key]` | `dim_product[product_key]` |
| `fact_inventory[product_key]` | `dim_product[product_key]` |

Mark `dim_date` as the date table (*Table tools → Mark as date table*, using `date`, which must be type Date).

## 3. DAX measures

```DAX
Measurable Orders = CALCULATE ( COUNTROWS ( fact_orders ), NOT ISBLANK ( fact_orders[is_late] ) )

Late Orders = CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_late] = 1 )

OTD % = DIVIDE ( [Measurable Orders] - [Late Orders], [Measurable Orders] )

Late % = DIVIDE ( [Late Orders], [Measurable Orders] )

Cost per Late Order = 13.32          -- from reports/cost_per_late_order.csv

Cost of Late Deliveries = [Late Orders] * [Cost per Late Order]

Carrier Spend = SUMX ( fact_orders, RELATED ( dim_carrier[rate_per_parcel] ) )

Backorder Rate = DIVIDE (
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[has_backorder] = 1 ),
    COUNTROWS ( fact_orders ) )

OTD % LY = CALCULATE ( [OTD %], SAMEPERIODLASTYEAR ( dim_date[date] ) )

OTD Change pts = ( [OTD %] - [OTD % LY] ) * 100
```

## 4. Suggested report page

| Visual | Fields |
|---|---|
| **KPI cards** | `OTD %`, `OTD Change pts`, `Cost of Late Deliveries`, `Backorder Rate` |
| **Line chart** | X: `dim_date[date]` (month), Y: `OTD %`, Legend: `dim_carrier[carrier]`. Add a constant line at 1 Jul 2024 labelled "BudgetPost introduced". |
| **Matrix (heatmap)** | Rows: `dim_carrier[carrier]`, Columns: `dim_region[region]`, Values: `Late %` with a single-hue colour scale (conditional formatting → background colour) |
| **Clustered bar** | Y: `dim_date[day_name]`, X: `Late %`, Legend: `fact_orders[after_cutoff]` |
| **Bar (import CSV)** | Load `reports/opportunity_levers.csv`; Y: `description`, X: `net_benefit_gbp`, conditional colour for positive vs negative |
| **Slicers** | `dim_date[year]`, `dim_region[is_remote]`, `fact_orders[channel]` |

Save the file as `reports/northbrook_dashboard.pbix` and add a screenshot to the README.
