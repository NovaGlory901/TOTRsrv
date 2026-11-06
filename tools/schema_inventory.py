"""Read-only inventory of every schema in the database, as the read-only `robots` role.

Run from TOTRsrv:  .venv\\Scripts\\python.exe tools\\schema_inventory.py
Runs only SELECTs on pg_catalog / information_schema plus count(*) on tables it is allowed to read, in a
read-only session. Prints no connection details. Objects the role cannot see are simply absent from the
catalog views it can read; the output says which tables it could not count.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402
import psycopg.sql  # noqa: E402

import db  # noqa: E402,F401  (loads .env)

SKIP = ("pg_toast", "pg_temp")


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def main():
    conn = db.get_conn()
    conn.read_only = True
    cur = conn.cursor()
    print("server:", rows(cur, "SELECT version() AS v")[0]["v"])
    print("role:", rows(cur, "SELECT current_user AS u")[0]["u"])

    print("\n== schemas ==")
    for r in rows(
        cur,
        "SELECT nspname, pg_get_userbyid(nspowner) AS owner, obj_description(oid, 'pg_namespace') AS comment "
        "FROM pg_namespace WHERE nspname NOT LIKE 'pg_%%' AND nspname <> 'information_schema' ORDER BY 1",
    ):
        print(f"  {r['nspname']}  owner={r['owner']}  comment={r['comment']}")

    print("\n== extensions ==")
    for r in rows(cur, "SELECT extname, extversion FROM pg_extension ORDER BY 1"):
        print(f"  {r['extname']} {r['extversion']}")

    print("\n== tablespaces ==")
    for r in rows(
        cur, "SELECT spcname, pg_get_userbyid(spcowner) AS owner FROM pg_tablespace ORDER BY 1"
    ):
        print(f"  {r['spcname']}  owner={r['owner']}")

    print("\n== relations (schema.name kind, tablespace, owner, comment) ==")
    rels = rows(
        cur,
        """
        SELECT n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner,
               t.spcname, obj_description(c.oid, 'pg_class') AS comment,
               has_table_privilege(c.oid, 'SELECT') AS can_select
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_tablespace t ON t.oid = c.reltablespace
        WHERE c.relkind IN ('r','v','m','S','p','f') AND n.nspname NOT LIKE 'pg_%%' AND n.nspname <> 'information_schema'
        ORDER BY 1, 2""",
    )
    for r in rels:
        n = ""
        if r["relkind"] in ("r", "p") and r["can_select"]:
            q = psycopg.sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
                psycopg.sql.Identifier(r["nspname"]), psycopg.sql.Identifier(r["relname"])
            )
            cur.execute(q)
            n = f" rows={cur.fetchone()['n']}"
        elif r["relkind"] in ("r", "p"):
            n = " rows=(no SELECT right)"
        print(
            f"  {r['nspname']}.{r['relname']} [{r['relkind']}] owner={r['owner']} ts={r['spcname']}{n}  {r['comment'] or ''}"
        )

    print("\n== columns ==")
    for r in rows(
        cur,
        """
        SELECT n.nspname, c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod) AS type,
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid) AS dflt, col_description(c.oid, a.attnum) AS comment
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r','p','v','m')
          AND n.nspname NOT LIKE 'pg_%%' AND n.nspname <> 'information_schema'
        ORDER BY 1, 2, 3""",
    ):
        print(
            f"  {r['nspname']}.{r['relname']}.{r['attname']} {r['type']}{' NOT NULL' if r['attnotnull'] else ''}"
            f"{' DEFAULT ' + r['dflt'] if r['dflt'] else ''}  {r['comment'] or ''}"
        )

    print("\n== constraints (PK, FK, UNIQUE, CHECK) ==")
    for r in rows(
        cur,
        """
        SELECT n.nspname, c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid) AS def
        FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%%' AND n.nspname <> 'information_schema' ORDER BY 1, 2, 4, 3""",
    ):
        print(f"  {r['nspname']}.{r['relname']} {r['contype']} {r['conname']}: {r['def']}")

    print("\n== indexes ==")
    for r in rows(
        cur,
        "SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname NOT LIKE 'pg_%%' AND schemaname <> 'information_schema' ORDER BY 1, 2, 3",
    ):
        print(f"  {r['indexdef']}")

    print("\n== views ==")
    for r in rows(
        cur,
        "SELECT schemaname, viewname, definition FROM pg_views "
        "WHERE schemaname NOT LIKE 'pg_%%' AND schemaname <> 'information_schema' ORDER BY 1, 2",
    ):
        print(f"  {r['schemaname']}.{r['viewname']}: {r['definition']}")

    print("\n== table grants (information_schema, only what this role can see) ==")
    for r in rows(
        cur,
        "SELECT table_schema, table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS p "
        "FROM information_schema.role_table_grants WHERE table_schema NOT IN ('pg_catalog','information_schema') "
        "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
    ):
        print(f"  {r['table_schema']}.{r['table_name']} -> {r['grantee']}: {r['p']}")

    print("\n== schema usage for this role ==")
    for r in rows(
        cur,
        "SELECT nspname, has_schema_privilege(nspname, 'USAGE') AS u FROM pg_namespace "
        "WHERE nspname NOT LIKE 'pg_%%' AND nspname <> 'information_schema' ORDER BY 1",
    ):
        print(f"  {r['nspname']}: USAGE={r['u']}")

    print("\n== roles ==")
    for r in rows(
        cur,
        "SELECT rolname, rolcanlogin, rolsuper FROM pg_roles WHERE rolname NOT LIKE 'pg_%%' ORDER BY 1",
    ):
        print(f"  {r['rolname']} login={r['rolcanlogin']} super={r['rolsuper']}")

    print("\n== search_path / db default ==")
    print("  ", rows(cur, "SHOW search_path")[0])
    conn.rollback()
    conn.close()


if __name__ == "__main__":
    main()
