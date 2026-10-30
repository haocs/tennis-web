"""Shared state uses explicit desired values plus optimistic concurrency."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Conflict(Exception):
    def __init__(self, current):
        self.current = current


class State:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
        finally:
            db.close()

    def all(self):
        with self.connection() as db:
            return {key: {"value": json.loads(value), "version": version} for key, value, version in db.execute("SELECT key,value,version FROM state")}

    def update(self, key, value, expected):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value,version FROM state WHERE key=?", (key,)).fetchone()
            current = {"value": json.loads(row[0]), "version": row[1]} if row else {"value": None, "version": 0}
            if expected != current["version"]:
                db.rollback()
                raise Conflict(current)
            version = current["version"] + 1
            db.execute("INSERT OR REPLACE INTO state VALUES(?,?,?)", (key, json.dumps(value), version))
            db.commit()
            return {"value": value, "version": version}

    def update_many(self, changes):
        """Apply one practice's recording assignments atomically, with no partial save."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for key, value, expected in changes:
                row = db.execute('SELECT value,version FROM state WHERE key=?',(key,)).fetchone()
                current = {'value':json.loads(row[0]),'version':row[1]} if row else {'value':None,'version':0}
                if current['version'] != expected:
                    db.rollback()
                    raise Conflict(current)
            for key, value, expected in changes:
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(key,json.dumps(value),expected+1))
            db.commit()

    def save_court(self, identity, value, expected):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT key,value,version FROM state WHERE key LIKE 'court:%'").fetchall()
            current = {'value':None,'version':0}
            for key, raw, version in rows:
                stored = json.loads(raw)
                if key == 'court:'+identity:
                    current = {'value':stored,'version':version}
                elif stored and stored['name'].casefold() == value['name'].casefold():
                    db.rollback()
                    raise ValueError('A saved court already has this name. Select it from the list.')
            if current['version'] != expected:
                db.rollback()
                raise Conflict(current)
            version = expected+1
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',('court:'+identity,json.dumps(value),version))
            db.commit()
            return {'id':identity,**value,'version':version}
