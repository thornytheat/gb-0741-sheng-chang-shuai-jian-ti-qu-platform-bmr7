import sqlite3
from contextlib import contextmanager

from fastapi import Request

SCHEMA = '''
CREATE TABLE IF NOT EXISTS source (id TEXT PRIMARY KEY, content BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source(id), version TEXT NOT NULL,
  basis TEXT, created_at TEXT NOT NULL, elapsed_ms INTEGER NOT NULL, digest TEXT NOT NULL, evidence BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_recent ON runs(created_at DESC);
CREATE TABLE IF NOT EXISTS basis_declarations (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  basis TEXT NOT NULL,
  note TEXT,
  declared_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparisons (
  id TEXT PRIMARY KEY,
  before_run_id TEXT NOT NULL REFERENCES runs(id),
  after_run_id TEXT NOT NULL REFERENCES runs(id),
  reference_runs TEXT NOT NULL,
  created_at TEXT NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  digest TEXT NOT NULL,
  evidence BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS comparisons_recent ON comparisons(created_at DESC);
CREATE TABLE IF NOT EXISTS publications (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  comparison_id TEXT NOT NULL REFERENCES comparisons(id),
  comparison_digest TEXT NOT NULL,
  scope TEXT NOT NULL,
  created_at TEXT NOT NULL,
  digest TEXT NOT NULL,
  evidence BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS publications_recent ON publications(seq DESC);
'''

# 旧库增量迁移：既有归档原字节与原证据保持可回看，只补登记列。
MIGRATIONS = (
    ('runs', 'basis', 'ALTER TABLE runs ADD COLUMN basis TEXT'),
)


def _migrate(connection):
    for table, column, statement in MIGRATIONS:
        columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
        if column not in columns:
            connection.execute(statement)


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.executescript(SCHEMA)
            _migrate(connection)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def get_store(request: Request) -> Store:
    return request.app.state.store
