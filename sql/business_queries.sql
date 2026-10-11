-- Northbrook Home: business questions answered in SQL (SQLite dialect)
-- Each query starts with a "-- name:" line; src/run_queries.py runs them all
-- and saves each result to reports/sql/<name>.csv

-- name: q1_otd_by_year
-- How has on-time delivery (OTD) changed year on year?
SELECT d.year,
       COUNT(*)                                   AS measurable_orders,
       ROUND(100.0 * (1 - AVG(f.is_late)), 1)     AS otd_pct,
       ROUND(AVG(f.days_late), 2)                 AS avg_days_late
FROM fact_orders f
JOIN dim_date d ON d.date_key = f.order_date_key
WHERE f.is_late IS NOT NULL
GROUP BY d.year
ORDER BY d.year;

-- name: q2_otd_monthly_by_carrier
-- Monthly OTD per carrier: when did performance break?
SELECT substr(d.date, 1, 7)                       AS month,
       c.carrier,
       COUNT(*)                                   AS orders,
       ROUND(100.0 * (1 - AVG(f.is_late)), 1)     AS otd_pct
FROM fact_orders f
JOIN dim_date d    ON d.date_key = f.order_date_key
JOIN dim_carrier c ON c.carrier_key = f.carrier_key
WHERE f.is_late IS NOT NULL
GROUP BY month, c.carrier
ORDER BY month, c.carrier;

-- name: q3_late_rate_carrier_region
-- Where is each carrier failing? Late rate by carrier x region (2025)
SELECT c.carrier,
       r.region,
       r.is_remote,
       COUNT(*)                                   AS orders,
       ROUND(100.0 * AVG(f.is_late), 1)           AS late_pct
FROM fact_orders f
JOIN dim_carrier c ON c.carrier_key = f.carrier_key
JOIN dim_region r  ON r.region_key = f.region_key
JOIN dim_date d    ON d.date_key = f.order_date_key
WHERE f.is_late IS NOT NULL AND d.year = 2025
GROUP BY c.carrier, r.region
ORDER BY late_pct DESC;

-- name: q4_cutoff_and_weekday
-- Do orders placed after the 14:00 cut-off, or late in the week, miss the promise?
SELECT d.day_name,
       f.after_cutoff,
       COUNT(*)                                   AS orders,
       ROUND(100.0 * AVG(f.is_late), 1)           AS late_pct
FROM fact_orders f
JOIN dim_date d ON d.date_key = f.order_date_key
WHERE f.is_late IS NOT NULL AND d.year = 2025
GROUP BY d.day_name, f.after_cutoff
ORDER BY CASE d.day_name WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
         WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END,
         f.after_cutoff;

-- name: q5_backorder_impact
-- How much do stock-outs (backorders) drive lateness, by category?
SELECT p.category,
       COUNT(DISTINCT l.order_id)                                              AS orders,
       ROUND(100.0 * AVG(l.backordered), 2)                                    AS backorder_line_pct,
       ROUND(100.0 * AVG(CASE WHEN f.has_backorder = 1 THEN f.is_late END), 1) AS late_pct_backordered,
       ROUND(100.0 * AVG(CASE WHEN f.has_backorder = 0 THEN f.is_late END), 1) AS late_pct_in_stock
FROM fact_order_lines l
JOIN fact_orders f ON f.order_id = l.order_id
JOIN dim_product p ON p.product_key = l.product_key
WHERE f.is_late IS NOT NULL
GROUP BY p.category
ORDER BY backorder_line_pct DESC;

-- name: q6_repeat_rate_after_late
-- Do customers whose FIRST order was late come back less?
WITH ranked AS (
    SELECT f.customer_key, f.is_late, f.order_date_key,
           ROW_NUMBER() OVER (PARTITION BY f.customer_key ORDER BY f.order_date_key, f.order_id) AS rn,
           COUNT(*)     OVER (PARTITION BY f.customer_key)                                      AS n_orders
    FROM fact_orders f
    WHERE f.status <> 'Cancelled'
)
SELECT CASE WHEN is_late = 1 THEN 'First order late' ELSE 'First order on time' END AS first_order,
       COUNT(*)                                          AS customers,
       ROUND(100.0 * AVG(CASE WHEN n_orders > 1 THEN 1.0 ELSE 0 END), 1) AS repeat_rate_pct
FROM ranked
WHERE rn = 1 AND is_late IS NOT NULL AND order_date_key < 20250701   -- allow 6 months to return
GROUP BY first_order;

-- name: q7_peak_vs_normal
-- How does the November/December peak strain fulfilment?
SELECT d.year,
       CASE WHEN d.is_peak = 1 THEN 'Peak' ELSE 'Normal' END AS period,
       COUNT(*) * 1.0 / COUNT(DISTINCT d.date)               AS orders_per_day,
       ROUND(100.0 * AVG(f.has_backorder), 1)                AS backorder_pct,
       ROUND(100.0 * (1 - AVG(f.is_late)), 1)                AS otd_pct
FROM fact_orders f
JOIN dim_date d ON d.date_key = f.order_date_key
WHERE f.is_late IS NOT NULL
GROUP BY d.year, period
ORDER BY d.year, period;

-- name: q8_carrier_cost_vs_service
-- Is the cheap carrier really cheaper once late deliveries are counted? (2025)
SELECT c.carrier,
       c.rate_per_parcel,
       COUNT(*)                                          AS parcels,
       ROUND(COUNT(*) * c.rate_per_parcel, 0)            AS carrier_spend_gbp,
       ROUND(100.0 * AVG(f.is_late), 1)                  AS late_pct,
       ROUND(AVG(f.order_value), 2)                      AS avg_order_value
FROM fact_orders f
JOIN dim_carrier c ON c.carrier_key = f.carrier_key
JOIN dim_date d    ON d.date_key = f.order_date_key
WHERE f.is_late IS NOT NULL AND d.year = 2025
GROUP BY c.carrier
ORDER BY c.rate_per_parcel;
