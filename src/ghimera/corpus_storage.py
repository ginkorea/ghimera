"""Owner-private transactional originals, passages, vectors and operation audit."""

import hashlib
import sqlite3
import struct
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import TypeAdapter

from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_types import BoundCorpusDocument, CorpusPassage
from ghimera.embedding_types import (
    EncodingBatch,
    EncodingCall,
    EncodingIntent,
    EncodingRecoveryState,
    Vector,
)
from ghimera.models import Document
from ghimera.private_database import PrivateDatabase

_VECTOR = TypeAdapter(Vector)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CorpusStorage:
    """The database is the sole durable authority; ANN state is a rebuildable view."""

    def __init__(self, config: CorpusConfig, *, create: bool) -> None:
        self.config = CorpusConfig.model_validate(config.model_dump())
        self._private = PrivateDatabase(
            config.directory,
            "corpus.sqlite",
            timeout=config.database_timeout_seconds,
            create=create,
        )
        try:
            if create:
                self._initialize()
                self._private.seal_directory()
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
            self._initialize_recovery()
        except BaseException:
            self.close()
            raise

    @property
    def db(self) -> sqlite3.Connection:
        return self._private.db

    def check(self) -> None:
        self._private.check()

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
        with self._private.writer():
            yield

    def close(self) -> None:
        self._private.close()

    def _initialize_recovery(self) -> None:
        exists = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='encoding_invocations'"
        ).fetchone()
        if self.config.encoding_recovery is None:
            if exists is not None:
                raise ValueError(
                    "durable encoding recovery cannot be disabled on an enrolled corpus"
                )
            return
        with self.transaction():
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS encoding_invocations("
                "id TEXT PRIMARY KEY, intent BLOB NOT NULL, input_chars INTEGER NOT NULL,"
                "status TEXT NOT NULL, reserved_bytes INTEGER NOT NULL, result BLOB,"
                "result_sha TEXT, call_id INTEGER REFERENCES calls(id))"
            )
        self.encoding_recovery_state()

    def encoding_recovery_state(self) -> EncodingRecoveryState | None:
        policy = self.config.encoding_recovery
        if policy is None:
            return None
        calls, chars, size, acknowledged, unresolved = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(input_chars),0),COALESCE(SUM(reserved_bytes),0),"
            "COALESCE(SUM(status='acknowledged'),0),COALESCE(SUM(status='unknown'),0)"
            " FROM encoding_invocations"
        ).fetchone()
        state = EncodingRecoveryState(
            schema="ghimera.encoding-recovery-state/1",
            reserved_calls=int(calls),
            reserved_input_chars=int(chars),
            stored_bytes=int(size),
            acknowledged=int(acknowledged),
            unresolved=int(unresolved),
        )
        if (
            state.reserved_calls > policy.max_calls
            or state.reserved_input_chars > policy.max_input_chars
            or state.stored_bytes > policy.max_stored_bytes
        ):
            raise ValueError(
                "persisted encoding reservations exceed the configured recovery allowance"
            )
        return state

    def reserve_encoding(self, intent: EncodingIntent) -> tuple[EncodingBatch, int] | None:
        """Commit an unknown reservation before contact, or return a validated local ACK."""
        policy = self.config.encoding_recovery
        if policy is None:
            raise ValueError("encoding recovery is not configured")
        intent = EncodingIntent.model_validate(intent.model_dump())
        if intent.corpus_id != self.identity:
            raise ValueError("encoding intent belongs to another corpus")
        payload = intent.model_dump_json().encode()
        with self.transaction():
            row = self.db.execute(
                "SELECT intent,status,result,result_sha,call_id FROM encoding_invocations"
                " WHERE id=?",
                (intent.identity,),
            ).fetchone()
            if row is not None:
                if row[0] != payload:
                    raise ValueError("stored encoding intent changed")
                if row[1] != "acknowledged":
                    raise ValueError(
                        "encoding invocation requires reconciliation; automatic replay refused"
                    )
                return self._acknowledged_encoding(intent, row[2], row[3], row[4])
            state = self.encoding_recovery_state()
            if state is None:
                raise ValueError("encoding recovery is not configured")
            # Reserve input plus the entire bounded result before invoking any adapter.
            # Finite IEEE-double JSON components can expand from short integer wire
            # literals. Thirty-two bytes covers a signed round-trippable double
            # plus its separator; this is a serialization invariant, not tuning.
            result_bound = max(
                intent.service.max_response_bytes,
                intent.service.dimensions * len(intent.texts) * 32,
            )
            result_bound += len(intent.service.model_dump_json().encode()) + 2048
            result_bound += 70 * intent.service.max_batch_texts
            reserved = len(payload) + result_bound
            if (
                state.reserved_calls + 1 > policy.max_calls
                or state.reserved_input_chars + intent.input_chars > policy.max_input_chars
                or state.stored_bytes + reserved > policy.max_stored_bytes
            ):
                raise ValueError("durable encoding allowance exhausted before contact")
            self.db.execute(
                "INSERT INTO encoding_invocations VALUES(?,?,?,'unknown',?,NULL,NULL,NULL)",
                (intent.identity, payload, intent.input_chars, reserved),
            )
        return None

    def _acknowledged_encoding(
        self, intent: EncodingIntent, result: object, sha: object, call_id: object
    ) -> tuple[EncodingBatch, int]:
        if not isinstance(result, bytes) or digest(result) != sha or type(call_id) is not int:
            raise ValueError("stored encoding acknowledgement changed or is incomplete")
        batch = EncodingBatch.model_validate_json(result)
        intent.validate_call(batch.call)
        row = self.db.execute("SELECT payload FROM calls WHERE id=?", (call_id,)).fetchone()
        if row is None or EncodingCall.model_validate_json(row[0]) != batch.call:
            raise ValueError("encoding acknowledgement lost its original audit lineage")
        return batch, call_id

    def acknowledge_encoding(
        self, operation: str, intent: EncodingIntent, batch: EncodingBatch
    ) -> int:
        """Persist observed vectors and audit atomically before returning them to the caller."""
        batch = EncodingBatch.model_validate(batch.model_dump())
        intent.validate_call(batch.call)
        result = batch.model_dump_json().encode()
        with self.transaction():
            row = self.db.execute(
                "SELECT intent,reserved_bytes FROM encoding_invocations"
                " WHERE id=? AND status='unknown'",
                (intent.identity,),
            ).fetchone()
            if row is None or row[0] != intent.model_dump_json().encode():
                raise ValueError("encoding result has no exact pre-contact intent")
            size = len(row[0]) + len(result)
            if size > int(row[1]):
                raise ValueError("encoding result exceeds its pre-contact storage reservation")
            call_id = self._audit(operation, intent.purpose, batch.call)
            self.db.execute(
                "UPDATE encoding_invocations SET status='acknowledged',reserved_bytes=?,"
                "result=?,result_sha=?,call_id=? WHERE id=?",
                (size, result, digest(result), call_id, intent.identity),
            )
        return call_id

    def refuse_encoding(self, intent: EncodingIntent, call: EncodingCall) -> None:
        # Observation of failure does not prove that the server did no work.
        # Keep the reservation charged, and never turn it into a retry allowance.
        with self.transaction():
            self.db.execute(
                "UPDATE encoding_invocations SET status=? WHERE id=? AND status='unknown'",
                (call.outcome if call.telemetry == "observed" else "unknown", intent.identity),
            )

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
        with self._private.transaction():
            yield

    def audit(self, operation: str, purpose: str, call: EncodingCall) -> int:
        self.check()
        with self.transaction():
            return self._audit(operation, purpose, call)

    def _audit(self, operation: str, purpose: str, call: EncodingCall) -> int:
        data = call.model_dump_json().encode()
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
            for passage_id, stored_vector in zip(ids, vectors, strict=True):
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
                if self.config.encoding_recovery is not None:
                    recovery = self.db.execute(
                        "SELECT intent,result,result_sha,call_id FROM encoding_invocations"
                        " WHERE call_id=(SELECT encoding_id FROM chunks WHERE id=?)"
                        " AND status='acknowledged'",
                        (passage_id,),
                    ).fetchone()
                    # Vectors created before opting in retain their original legacy audit.
                    if recovery is not None:
                        intent = EncodingIntent.model_validate_json(recovery[0])
                        batch, _ = self._acknowledged_encoding(
                            intent, recovery[1], recovery[2], recovery[3]
                        )
                        if stored_vector != batch.vectors[position]:
                            raise ValueError(
                                "stored vector differs from its exact acknowledged result"
                            )
            return generation, tuple(ids), tuple(vectors)
        finally:
            self.db.rollback()
