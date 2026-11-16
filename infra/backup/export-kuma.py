#!/usr/bin/env python3
"""Export a sanitized Kuma SQLite backup with monitoring history and schema markers."""

import sqlite3, sys, pathlib, urllib.parse

src, out = sys.argv[1:]
a = sqlite3.connect("file:" + src + "?mode=ro", uri=True)
b = sqlite3.connect(out)
a.backup(b)
a.close()
# Clear tables outside this list; retain migration markers needed on restore.
keep = {
    "monitor",
    "heartbeat",
    "stat_daily",
    "stat_hourly",
    "stat_minutely",
    "status_page",
    "monitor_group",
    "group",
    "incident",
    "maintenance",
    "monitor_maintenance",
    "maintenance_status_page",
    "monitor_status_page",
    "status_page_cname",
    "monitor_tag",
    "tag",
}
# Kuma 2 uses Knex's migration history to open an already migrated database.
keep.update({"knex_migrations", "knex_migrations_lock"})
for (table,) in b.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall():
    if table.startswith("sqlite_"):
        continue
    quoted = '"' + table.replace('"', '""') + '"'
    if table == "setting":
        # Keep the schema markers so restoring a copy does not replay applied patches.
        b.execute(
            "DELETE FROM setting WHERE key IS NULL OR key NOT IN (?, ?)",
            ("database_version", "databasePatchedFiles"),
        )
        continue
    if table not in keep:
        b.execute("DELETE FROM " + quoted)
        continue
    if table == "monitor":
        allowed = {
            "id",
            "name",
            "active",
            "type",
            "interval",
            "retry_interval",
            "resend_interval",
            "maxretries",
            "weight",
            "upside_down",
            "accepted_statuscodes_json",
            "method",
            "hostname",
            "port",
            "url",
            "description",
            "snmp_version",
        }
        for col in b.execute("PRAGMA table_info(" + quoted + ")").fetchall():
            name = col[1]
            if name not in allowed:
                # Respect NOT NULL constraints; empty value carries no credentials.
                value = 0 if "INT" in col[2].upper() else ""
                b.execute(
                    "UPDATE " + quoted + ' SET "' + name.replace('"', '""') + '"=?',
                    (value,),
                )
        for ident, url in b.execute("SELECT id,url FROM monitor").fetchall():
            if url:
                u = urllib.parse.urlsplit(url)
                host = u.hostname or ""
                host += (":" + str(u.port)) if u.port else ""
                b.execute(
                    "UPDATE monitor SET url=? WHERE id=?",
                    (urllib.parse.urlunsplit((u.scheme, host, u.path, "", "")), ident),
                )
b.commit()
b.execute("VACUUM")
b.close()
