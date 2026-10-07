"""Owner-private transactional originals, passages, vectors and operation audit."""

import fcntl
import hashlib
import os
import sqlite3
import stat
import struct
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import TypeAdapter

from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_types import BoundCorpusDocument, CorpusPassage
from ghimera.embedding_types import EncodingCall, Vector
from ghimera.models import Document

_VECTOR = TypeAdapter(Vector)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CorpusStorage:
    """The database is the sole durable authority; ANN state is a rebuildable view."""

    def __init__(self, config: CorpusConfig, *, create: bool) -> None:
        self.config = CorpusConfig.model_validate(config.model_dump())
        path = config.directory
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ValueError("corpus storage cannot traverse symlinks")
        if create:
            path.mkdir(mode=0o700, exist_ok=False)
        self._directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._connection: sqlite3.Connection | None = None
        self._database = -1
        try:
            self.check()
            self._database = os.open(
                "corpus.sqlite",
                os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0),
                0o600,
                dir_fd=self._directory,
            )
            self.check()
            # SQLite journals are created inside the checked directory descriptor.
            database = f"/proc/self/fd/{self._directory}/corpus.sqlite"
            self._connection = sqlite3.connect(database, timeout=config.database_timeout_seconds)
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA synchronous=FULL")
            if create:
                self._initialize()
                os.fsync(self._directory)
                parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            else:
                policy = self.db.execute("SELECT policy FROM state WHERE id=1").fetchone()
                if policy is None or policy[0] != config.recipe_identity:
                    raise ValueError("corpus requires its exact recorded model and recipe")
                self.generation()
                _ = self.identity  # Validate durable identity before permitting model spend.
                count_docs, count_chunks, source_bytes, vector_bytes = self.counts()
                if (
                    count_docs > config.max_documents
                    or count_chunks > config.max_chunks
                    or source_bytes > config.max_stored_document_bytes
                    or vector_bytes > config.max_vector_bytes
                ):
                    raise ValueError("existing corpus exceeds the newly configured capacity")
        except BaseException:
            self.close()
            raise

    @property
    def db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ValueError("corpus storage is closed")
        return self._connection

    def check(self) -> None:
        info = os.fstat(self._directory)
        named = self.config.directory.lstat()
        if (
            info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError("corpus directory must remain at its owner-private identity")
        if self._database >= 0:
            info = os.fstat(self._database)
            named = os.stat("corpus.sqlite", dir_fd=self._directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise ValueError("corpus database must remain owner-private and non-linked")

    def _initialize(self) -> None:
        self.db.executescript(
            "CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),"
            " schema TEXT NOT NULL, policy TEXT NOT NULL,"
            " generation INTEGER NOT NULL, recipe BLOB NOT NULL, corpus_id TEXT NOT NULL);"
            "CREATE TABLE documents(id TEXT PRIMARY KEY, payload BLOB NOT NULL);"
            "CREATE TABLE chunks(id INTEGER PRIMARY KEY, document_id TEXT NOT NULL"
            " REFERENCES documents(id), payload BLOB NOT NULL, vector BLOB NOT NULL,"
            " vector_sha TEXT NOT NULL, encoding_id INTEGER NOT NULL REFERENCES calls(id),"
            " batch_index INTEGER NOT NULL);"
            "CREATE TABLE operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL,"
            " input_sha TEXT NOT NULL, status TEXT NOT NULL, reserved_entries INTEGER NOT NULL,"
            " reserved_bytes INTEGER NOT NULL);"
            "CREATE TABLE calls(id INTEGER PRIMARY KEY, operation TEXT NOT NULL"
            " REFERENCES operations(id), purpose TEXT NOT NULL, payload BLOB NOT NULL);"
        )
        self.db.execute(
            "INSERT INTO state VALUES(1,?,?,0,?,?)",
            (
                "ghimera.corpus-store/1",
                self.config.recipe_identity,
                self.config.model_dump_json().encode(),
                uuid.uuid4().hex,
            ),
        )
        self.db.commit()

    @contextmanager
    def writer(self) -> Iterator[None]:
        self.check()
        # Independent opens are essential: flock on a shared fd is not exclusion.
        fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=self._directory)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._database >= 0:
            os.close(self._database)
            self._database = -1
        if self._directory >= 0:
            os.close(self._directory)
            self._directory = -1

    def counts(self) -> tuple[int, int, int, int]:
        self.check()
        docs, source_bytes = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM documents"
        ).fetchone()
        chunks, vector_bytes = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(vector)),0) FROM chunks"
        ).fetchone()
        return int(docs), int(chunks), int(source_bytes), int(vector_bytes)

    def has_document(self, document_id: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone()
            is not None
        )

    def document(self, document_id: str) -> Document:
        self.check()
        row = self.db.execute(
            "SELECT payload FROM documents WHERE id=? AND length(payload)<=?",
            (document_id, self.config.max_document_bytes),
        ).fetchone()
        if (
            row is None
            or not isinstance(row[0], bytes)
            or len(row[0]) > self.config.max_document_bytes
        ):
            raise ValueError("original document is absent or exceeds its bound")
        data = row[0]
        if digest(data) != document_id:
            raise ValueError("original document bytes changed")
        document = Document.model_validate_json(data)
        if document.verdict.decision != "accept":
            raise ValueError("only accepted originals are searchable")
        return document

    def passage(self, passage_id: int) -> CorpusPassage:
        passage = self._passage(passage_id)
        passage.validate_binding(BoundCorpusDocument(self.document(passage.document_id)))
        return passage

    def _passage(self, passage_id: int) -> CorpusPassage:
        row = self.db.execute(
            "SELECT payload FROM chunks WHERE id=? AND length(payload)<=?",
            (passage_id, self.config.max_document_bytes),
        ).fetchone()
        if (
            row is None
            or not isinstance(row[0], bytes)
            or len(row[0]) > self.config.max_document_bytes
        ):
            raise ValueError("passage is absent or exceeds its bound")
        return CorpusPassage.model_validate_json(row[0])

    def _audit_room(self, *, entries: int, size: int) -> None:
        count, current = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM calls"
        ).fetchone()
        operations, pending, reserved = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(reserved_entries),0),"
            "COALESCE(SUM(reserved_bytes),0) FROM operations"
        ).fetchone()
        if (
            int(count) + int(operations) + int(pending) + entries > self.config.max_audit_entries
            or int(current) + int(reserved) + size > self.config.max_audit_bytes
        ):
            raise ValueError("corpus audit capacity exhausted; export/rotate explicitly")

    def start(self, operation: str, *, kind: str, input_sha: str, calls: int) -> None:
        self.check()
        # Reserve the maximum possible response evidence before any model call.
        maximum_call = len(self.config.encoder.model_dump_json().encode()) + 1024
        maximum_call += self.config.encoder.max_response_bytes
        maximum_call += 70 * self.config.encoder.max_batch_texts
        maximum_query = len(self.config.query_encoder.model_dump_json().encode()) + 1094
        maximum_query += self.config.query_encoder.max_response_bytes
        maximum = calls * max(maximum_call, maximum_query)
        with self.transaction():
            self._audit_room(entries=calls + 1, size=maximum)
            self.db.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,?)",
                (operation, kind, input_sha, "pending", calls, maximum),
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def audit(self, operation: str, purpose: str, call: EncodingCall) -> int:
        self.check()
        data = call.model_dump_json().encode()
        with self.transaction():
            row = self.db.execute(
                "SELECT reserved_entries,reserved_bytes FROM operations"
                " WHERE id=? AND status='pending'",
                (operation,),
            ).fetchone()
            if row is None or int(row[0]) < 1 or int(row[1]) < len(data):
                raise ValueError("encoding audit exceeds its pre-call reservation")
            cursor = self.db.execute(
                "INSERT INTO calls(operation,purpose,payload) VALUES(?,?,?)",
                (operation, purpose, data),
            )
            self.db.execute(
                "UPDATE operations SET reserved_entries=reserved_entries-1,"
                "reserved_bytes=reserved_bytes-? WHERE id=?",
                (len(data), operation),
            )
            identity = cursor.lastrowid
            if identity is None:
                raise ValueError("encoding audit was not acknowledged")
            return identity

    def terminal(self, operation: str, status: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE operations SET status=?,reserved_entries=0,reserved_bytes=0 WHERE id=?",
                (status, operation),
            )

    def commit(
        self,
        operation: str,
        documents: tuple[tuple[str, bytes], ...],
        chunks: tuple[tuple[CorpusPassage, tuple[float, ...], int, int], ...],
    ) -> int:
        self.check()
        with self.db:
            self.db.executemany("INSERT INTO documents VALUES(?,?)", documents)
            for passage, vector, encoding_id, batch_index in chunks:
                data = struct.pack(f"<{self.config.encoder.dimensions}d", *vector)
                self.db.execute(
                    "INSERT INTO chunks(document_id,payload,vector,vector_sha,"
                    "encoding_id,batch_index)"
                    " VALUES(?,?,?,?,?,?)",
                    (
                        passage.document_id,
                        passage.model_dump_json().encode(),
                        data,
                        digest(data),
                        encoding_id,
                        batch_index,
                    ),
                )
            if documents:
                self.db.execute("UPDATE state SET generation=generation+1 WHERE id=1")
            self.db.execute(
                "UPDATE operations SET status='committed',reserved_entries=0,reserved_bytes=0"
                " WHERE id=?",
                (operation,),
            )
        return self.generation()

    def generation(self) -> int:
        schema, policy, generation = self.db.execute(
            "SELECT schema,policy,generation FROM state WHERE id=1"
        ).fetchone()
        if schema != "ghimera.corpus-store/1" or policy != self.config.recipe_identity:
            raise ValueError("corpus metadata does not match its recipe")
        return int(generation)

    @property
    def identity(self) -> str:
        value = self.db.execute("SELECT corpus_id FROM state WHERE id=1").fetchone()[0]
        if not isinstance(value, str) or len(value) != 32 or uuid.UUID(value).hex != value:
            raise ValueError("corpus identity is missing")
        return value

    def vectors(self) -> tuple[int, tuple[int, ...], tuple[tuple[float, ...], ...]]:
        self.check()
        # The generation and vectors are one read snapshot even beside another writer.
        self.db.execute("BEGIN")
        try:
            generation = self.generation()
            count, total = self.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(vector)),0) FROM chunks"
            ).fetchone()
            if int(count) > self.config.max_chunks or int(total) > self.config.max_vector_bytes:
                raise ValueError("stored passages exceed corpus capacity")
            rows = self.db.execute("SELECT id,vector,vector_sha FROM chunks ORDER BY id").fetchall()
            vectors: list[tuple[float, ...]] = []
            ids: list[int] = []
            size = 0
            for identity, data, sha in rows:
                if not isinstance(data, bytes):
                    raise ValueError("stored vector is not a byte record")
                size += len(data)
                if size > self.config.max_vector_bytes or digest(data) != sha:
                    raise ValueError("stored vectors exceed their bound or changed")
                if len(data) != self.config.encoder.dimensions * 8:
                    raise ValueError("stored vector dimension does not match its model")
                vectors.append(
                    _VECTOR.validate_python(
                        struct.unpack(f"<{self.config.encoder.dimensions}d", data)
                    )
                )
                ids.append(int(identity))
            source: BoundCorpusDocument | None = None
            for passage_id in ids:
                passage = self._passage(passage_id)
                if source is None or source.identity != passage.document_id:
                    source = BoundCorpusDocument(self.document(passage.document_id))
                passage.validate_binding(source)
                row = self.db.execute(
                    "SELECT calls.payload,chunks.batch_index FROM chunks JOIN calls"
                    " ON calls.id=chunks.encoding_id WHERE chunks.id=?"
                    " AND length(calls.payload)<=?",
                    (passage_id, self.config.max_audit_bytes),
                ).fetchone()
                if row is None or not isinstance(row[0], bytes):
                    raise ValueError("vector has no durable encoding observation")
                call = EncodingCall.model_validate_json(row[0])
                position = int(row[1])
                if (
                    call.outcome != "success"
                    or not self.config.same_passage_space(call.service)
                    or not 0 <= position < len(call.input_sha256)
                    or call.input_sha256[position]
                    != digest((self.config.encoder.text_prefix + passage.text).encode())
                ):
                    raise ValueError("vector's native input does not match its encoding call")
            return generation, tuple(ids), tuple(vectors)
        finally:
            self.db.rollback()
