"""Tests for PendingReveal DB helpers."""
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine

import db
from db import Base


@pytest.fixture(autouse=True)
def in_memory_db():
    """Swap db.engine for a fresh in-memory SQLite for each test."""
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    with patch.object(db, "engine", test_engine):
        yield test_engine


class TestAddAndGet:
    def test_returns_integer_id(self):
        rid = db.add_pending_reveal(100, 200, "text", 9999.0)
        assert isinstance(rid, int)

    def test_stored_fields(self):
        db.add_pending_reveal(111, 222, "reveal me", 1234567890.5)
        reveals = db.get_pending_reveals()
        assert len(reveals) == 1
        r = reveals[0]
        assert r.chat_id == 111
        assert r.message_id == 222
        assert r.reveal_text == "reveal me"
        assert r.reveal_at == pytest.approx(1234567890.5)

    def test_empty_returns_empty_list(self):
        assert db.get_pending_reveals() == []

    def test_multiple_rows(self):
        db.add_pending_reveal(1, 1, "a", 1.0)
        db.add_pending_reveal(2, 2, "b", 2.0)
        db.add_pending_reveal(3, 3, "c", 3.0)
        assert len(db.get_pending_reveals()) == 3

    def test_ids_are_unique(self):
        id1 = db.add_pending_reveal(1, 1, "a", 0.0)
        id2 = db.add_pending_reveal(2, 2, "b", 0.0)
        assert id1 != id2


class TestDelete:
    def test_deletes_target_row(self):
        rid = db.add_pending_reveal(1, 1, "x", 0.0)
        db.delete_pending_reveal(rid)
        assert db.get_pending_reveals() == []

    def test_deletes_only_target(self):
        id1 = db.add_pending_reveal(1, 1, "a", 0.0)
        id2 = db.add_pending_reveal(2, 2, "b", 0.0)
        db.delete_pending_reveal(id1)
        remaining = db.get_pending_reveals()
        assert len(remaining) == 1
        assert remaining[0].id == id2

    def test_delete_nonexistent_is_silent(self):
        db.delete_pending_reveal(99999)  # should not raise
