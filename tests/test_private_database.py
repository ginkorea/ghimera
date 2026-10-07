"""Behavioral shared storage boundary: owner modes, descriptors and rollback."""

import os

import pytest

from ghimera.private_database import PrivateDatabase


def test_transaction_rollback_and_independent_writer_exclusion(tmp_path):
    path = tmp_path / "private"
    first = PrivateDatabase(path, "test.sqlite", timeout=2.0, create=True)
    try:
        first.db.execute("CREATE TABLE things(value INTEGER)")
        first.db.commit()
        first.seal_directory()
        second = PrivateDatabase(path, "test.sqlite", timeout=2.0, create=False)
        try:
            with first.writer():
                with pytest.raises(BlockingIOError):
                    with second.writer():
                        raise AssertionError("a second writer must not enter")
            with pytest.raises(ValueError):
                with first.transaction():
                    first.db.execute("INSERT INTO things VALUES(1)")
                    raise ValueError("fixture failure")
            assert second.db.execute("SELECT * FROM things").fetchall() == []
            with first.transaction():
                first.db.execute("INSERT INTO things VALUES(2)")
            assert second.db.execute("SELECT * FROM things").fetchall() == [(2,)]
        finally:
            second.close()
    finally:
        first.close()
        first.close()


def test_public_modes_and_hardlinks_refuse_without_opening_a_connection(tmp_path):
    path = tmp_path / "private"
    store = PrivateDatabase(path, "test.sqlite", timeout=2.0, create=True)
    try:
        path.chmod(0o755)
        with pytest.raises(ValueError, match="owner-private"):
            store.check()
        path.chmod(0o700)
        (path / "test.sqlite").chmod(0o644)
        with pytest.raises(ValueError, match="owner-private"):
            store.check()
        (path / "test.sqlite").chmod(0o600)
        os.link(path / "test.sqlite", tmp_path / "foreign-copy")
        with pytest.raises(ValueError, match="non-linked"):
            store.check()
    finally:
        store.close()


def test_inode_replacement_and_symlink_ancestors_refuse(tmp_path):
    path = tmp_path / "private"
    store = PrivateDatabase(path, "test.sqlite", timeout=2.0, create=True)
    try:
        (path / "test.sqlite").rename(path / "old.sqlite")
        (path / "test.sqlite").touch(mode=0o600)
        with pytest.raises(ValueError, match="non-linked"):
            store.check()
    finally:
        store.close()
    (tmp_path / "alias").symlink_to(path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        PrivateDatabase(tmp_path / "alias", "test.sqlite", timeout=2.0, create=False)
