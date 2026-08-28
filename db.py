"""
SQLite connection helper.

Single place that owns connection creation so every connection gets the
same pragmas (foreign keys ON, WAL mode) and the same row factory. No ORM
is used — repositories/ hold hand-written parameterized SQL against
connections obtained here.
"""
import sqlite3
import contextlib

import config


def get_connection():
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # Off by default in sqlite3 -- must be set on every connection.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextlib.contextmanager
def connect():
    """Context manager: yields a connection, commits on clean exit, rolls
    back and re-raises on exception, always closes."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextlib.contextmanager
def connect_immediate():
    """Like connect(), but opens the transaction with BEGIN IMMEDIATE so the
    write lock is acquired up front. Use this for any read-check-then-write
    sequence (e.g. marking a PO line received) where a second concurrent
    request must not be able to interleave between the check and the write.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
