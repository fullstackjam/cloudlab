import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class KumaExportTest(unittest.TestCase):
    def test_export_keeps_schema_markers_and_removes_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.db"
            exported = Path(folder) / "exported.db"
            with closing(sqlite3.connect(source)) as db:
                db.executescript("""
                    CREATE TABLE setting (id INTEGER PRIMARY KEY, key TEXT, value TEXT, type TEXT);
                    INSERT INTO setting VALUES (1, 'database_version', '10', 'general');
                    INSERT INTO setting VALUES (2, 'databasePatchedFiles', '{}', 'general');
                    INSERT INTO setting VALUES (3, 'jwtSecret', ' fixture ', 'general');
                    CREATE TABLE user (id INTEGER PRIMARY KEY, password TEXT);
                    INSERT INTO user VALUES (1, ' fixture ');
                    CREATE TABLE knex_migrations (id INTEGER PRIMARY KEY, name TEXT);
                    INSERT INTO knex_migrations VALUES (1, '001-test.js');
                    CREATE TABLE knex_migrations_lock (id INTEGER PRIMARY KEY, is_locked INTEGER);
                    INSERT INTO knex_migrations_lock VALUES (1, 0);
                    CREATE TABLE monitor (
                        id INTEGER PRIMARY KEY, name TEXT, url TEXT, password TEXT,
                        snmp_version TEXT CHECK (snmp_version IN ('1', '2c', '3'))
                    );
                    INSERT INTO monitor VALUES (1, 'test', 'https://name:demo@example.org/status?check=demo', ' fixture ', '2c');
                """)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "export-data.py"), str(source), str(exported)],
                check=True,
            )
            with closing(sqlite3.connect(exported)) as db:
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
                self.assertEqual(
                    dict(db.execute("SELECT key, value FROM setting")),
                    {"database_version": "10", "databasePatchedFiles": "{}"},
                )
                self.assertEqual(db.execute("SELECT COUNT(*) FROM user").fetchone(), (0,))
                self.assertEqual(db.execute("SELECT name FROM knex_migrations").fetchone(), ("001-test.js",))
                self.assertEqual(db.execute("SELECT is_locked FROM knex_migrations_lock").fetchone(), (0,))
                self.assertEqual(
                    db.execute("SELECT name, url, password, snmp_version FROM monitor").fetchone(),
                    ("test", "https://example.org/status", "", "2c"),
                )
