"""SQLite-based deduplication store.

Tujuan: skip lead/buyer yang sudah pernah muncul di run sebelumnya supaya
setiap run cuma fokus ke leads BARU (fresh leads only). DB di-store di
`output/dedup.db` — portable, single file, no setup.

Tables:
    leads_seen  (domain TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, runs INTEGER)
    buyers_seen (domain TEXT, email TEXT, first_seen TEXT, last_seen TEXT, runs INTEGER,
                 PRIMARY KEY(domain, email))

Public API (semua aman thread-wise via 1 koneksi per call):
    DedupDB(path).is_lead_seen(domain) -> bool
    DedupDB(path).mark_lead(domain)
    DedupDB(path).filter_new_leads(domains) -> list[str]            # convenience
    DedupDB(path).is_buyer_seen(domain, email) -> bool
    DedupDB(path).mark_buyer(domain, email)
    DedupDB(path).stats() -> dict
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable


DEFAULT_DB_PATH = "output/dedup.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads_seen (
    domain      TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    runs        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS buyers_seen (
    domain      TEXT NOT NULL,
    email       TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    runs        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (domain, email)
);

CREATE INDEX IF NOT EXISTS idx_buyers_domain ON buyers_seen(domain);
"""


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


class DedupDB:
    """Tiny wrapper. 1 instance = 1 db path. Method-level open/close — aman."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = str(path)
        # ensure dir
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # init schema
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------
    # Leads
    # ---------------------------------------------------------------
    def is_lead_seen(self, domain: str) -> bool:
        d = (domain or "").strip().lower()
        if not d:
            return False
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM leads_seen WHERE domain=? LIMIT 1", (d,)
            ).fetchone()
        return row is not None

    def mark_lead(self, domain: str) -> None:
        d = (domain or "").strip().lower()
        if not d:
            return
        now = _now()
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO leads_seen(domain, first_seen, last_seen, runs)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(domain) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    runs      = leads_seen.runs + 1
                """,
                (d, now, now),
            )

    def filter_new_leads(self, domains: Iterable[str]) -> tuple[list[str], list[str]]:
        """Return (fresh_domains, skipped_domains). Order preserved."""
        fresh: list[str] = []
        skipped: list[str] = []
        for raw in domains:
            d = (raw or "").strip().lower()
            if not d:
                continue
            if self.is_lead_seen(d):
                skipped.append(d)
            else:
                fresh.append(d)
        return fresh, skipped

    # ---------------------------------------------------------------
    # Buyers
    # ---------------------------------------------------------------
    def is_buyer_seen(self, domain: str, email: str) -> bool:
        d = (domain or "").strip().lower()
        e = (email or "").strip().lower()
        if not d or not e:
            return False
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM buyers_seen WHERE domain=? AND email=? LIMIT 1",
                (d, e),
            ).fetchone()
        return row is not None

    def mark_buyer(self, domain: str, email: str) -> None:
        d = (domain or "").strip().lower()
        e = (email or "").strip().lower()
        if not d or not e:
            return
        now = _now()
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO buyers_seen(domain, email, first_seen, last_seen, runs)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(domain, email) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    runs      = buyers_seen.runs + 1
                """,
                (d, e, now, now),
            )

    # ---------------------------------------------------------------
    # Stats
    # ---------------------------------------------------------------
    def stats(self) -> dict:
        with self._conn() as c:
            leads = c.execute("SELECT COUNT(*) FROM leads_seen").fetchone()[0]
            buyers = c.execute("SELECT COUNT(*) FROM buyers_seen").fetchone()[0]
        return {"leads_seen": leads, "buyers_seen": buyers, "db_path": self.path}
