"""Run every query in sql/business_queries.sql and save results to reports/sql/."""
import re
import sqlite3

import pandas as pd

from config import DB_PATH, REPORTS, SQL_DIR


def load_queries(path=SQL_DIR / "business_queries.sql") -> dict:
    text = path.read_text()
    parts = re.split(r"^-- name:\s*(\w+)\s*$", text, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def main():
    out = REPORTS / "sql"
    out.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        for name, sql in load_queries().items():
            df = pd.read_sql_query(sql, con)
            df.to_csv(out / f"{name}.csv", index=False)
            print(f"\n== {name} ({len(df)} rows)")
            print(df.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
