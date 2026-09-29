"""Small DB-API boundary; PostgreSQL uses native transactions and full-text indexes."""
import re
import sqlite3
from pathlib import Path
import psycopg


class Row:
    def __init__(self, names, values):
        self.names, self.values = names, values
    def __getitem__(self, key):
        return self.values[key] if isinstance(key, int) else self.values[self.names.index(key)]
    def keys(self):
        return self.names
    def __iter__(self):
        return iter(self.values)


def row_factory(cursor):
    names = [d.name for d in cursor.description or []]
    return lambda values: Row(names, values)


class PostgresConnection:
    def __init__(self, dsn):
        self.connection = psycopg.connect(dsn, autocommit=True, row_factory=row_factory)
        self.transactions = []
    def execute(self, query, params=()):
        # The application owns these SQL templates; user data is always bound separately.
        query = query.replace('?', '%s')
        query = re.sub(r"json_extract\(body,'\$\.([a-z_]+)'\)", r"(body::jsonb->>'\1')", query)
        return self.connection.execute(query, params)
    def executescript(self, schema):
        schema = re.sub(r'PRAGMA[^;]+;', '', schema)
        schema = schema.replace('id INTEGER PRIMARY KEY, time', 'id BIGSERIAL PRIMARY KEY, time')
        schema = re.sub(r'CREATE VIRTUAL TABLE[^;]+;', "CREATE TABLE IF NOT EXISTS record_fts(key TEXT PRIMARY KEY, content TEXT); CREATE INDEX IF NOT EXISTS record_text_gin ON record_fts USING gin(to_tsvector('simple', content));", schema)
        with self.connection.transaction():
            self.connection.execute(schema)
    def __enter__(self):
        transaction = self.connection.transaction()
        self.transactions.append(transaction)
        transaction.__enter__()
        return self
    def __exit__(self, *args):
        return self.transactions.pop().__exit__(*args)
    def close(self):
        self.connection.close()


def connect(target):
    value = str(target)
    if value.startswith(('postgresql://', 'postgres://')) or 'dbname=' in value:
        return 'postgresql', PostgresConnection(value)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, check_same_thread=False, timeout=30)
    db.row_factory = sqlite3.Row
    return 'sqlite', db


integrity_errors = (sqlite3.IntegrityError, psycopg.IntegrityError)
