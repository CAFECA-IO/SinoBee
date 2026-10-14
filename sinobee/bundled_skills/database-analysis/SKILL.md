---
name: database-analysis
description: Answers questions from the user's databases (PostgreSQL, MySQL/MariaDB, SQLite) - inspect the schema, write read-only SQL, show the SQL, then report tables and charts. Use when the user asks about data, numbers, customers, orders, trends or reports stored in a database (資料庫、查詢、報表、統計、SQL).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: database, sql, analysis, report
  sinobee-triggers: 資料庫, 查詢, 報表, 統計, 營收, 客戶, 訂單, SQL, database, query, report
  sinobee-requires-tools: db_query
  author: sinobee
---

# When to Use
The answer lives in a saved database connection: "上個月哪個客戶下單最多", "每月營收趨勢", "哪些商品缺貨".
If the user has no connection yet, ask them to add one in the 資料庫 (Database) tab or paste a connection
string - never ask them to type a password into the chat.

# Procedure
1. `db_list` to pick the connection. If a skill named `db-<name>` exists for it, read it first: it has table
   notes and saved queries written for this database.
2. `db_schema` for the connection, then `db_schema` with `table` for the tables you will use (columns, keys).
   Never guess column names.
3. Write ONE read-only statement (SELECT / WITH). Aggregate in SQL (GROUP BY, window functions) instead of
   fetching many rows; add LIMIT. Use the dialect of the connection (PostgreSQL `date_trunc`, MySQL `DATE_FORMAT`,
   SQLite `strftime`).
4. Show the SQL to the user, then run it with `db_query`.
5. Check the result makes sense (row counts, NULLs, units, time zone). If not, fix the SQL and run it again.
6. Answer in the user's language: a short conclusion first, then a markdown table; for trends or rankings also
   draw a chart (a ```mermaid xychart-beta block, or a PNG with python/matplotlib saved in the workspace).
7. If the query is likely to be reused, save it with `db_save_query`. When the user wants to keep using this
   database, offer `db_make_tool` (table notes + saved queries + a skill for this database).

# Without SinoBee's database tools
Agents that only have a shell can use the same connections through SinoBee's CLI (read-only, passwords stay
in SinoBee's secrets.json):
- `sinobee db list`
- `sinobee db schema <connection> [table]`
- `sinobee db query <connection> "<SELECT …>" --format md|csv|json --limit 100`

# Pitfalls
- Writes (INSERT/UPDATE/DELETE/DDL) need `db_execute`, the connection's write permission and the user's
  approval every time. Never try to work around the read-only guard.
- Large tables: count first (`SELECT count(*)`), then sample or aggregate.
- Money and quantities: keep the database's precision; say the currency and unit.

# Verification
Every number in the answer comes from a query result shown in this conversation, and the SQL is visible.
