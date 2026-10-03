"""Additive schema upgrades that two processes can run on the same SQLite file at once (standard library only)."""
from __future__ import annotations

import re

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


def add_missing_columns(connection, table: str, columns: dict[str, str]) -> None:
    """Add each column ``table`` lacks, checked and added under one write lock.

    Two openers of a new or older file used to both see a column missing and both add it; the second failed with
    'duplicate column name'. BEGIN IMMEDIATE makes the second wait (the connection's busy timeout) and then find the
    column present. A file that already has every column is only read. The connection must not be inside a transaction.
    """
    if not _NAME.match(table) or not all(_NAME.match(name) for name in columns):
        raise ValueError('Table and column names must be plain identifiers')
    if connection.in_transaction:
        raise RuntimeError('add_missing_columns needs a connection outside a transaction')
    if set(columns) <= {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}:
        return  # nothing to add: no write lock, so an opener never waits behind another writer for nothing
    connection.execute('BEGIN IMMEDIATE')
    try:
        present = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for name, declaration in columns.items():
            if name not in present:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
    except BaseException:
        connection.execute('ROLLBACK')
        raise
    connection.execute('COMMIT')
