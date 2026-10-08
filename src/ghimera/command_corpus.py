"""Explicit corpus composition around the unchanged native collector command."""

import asyncio
import hashlib
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.command import CommandOptions, CredentialBindings, execute
from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointReceipt
from ghimera.corpus import EvidenceCorpus
from ghimera.corpus_config import CorpusConfig
from ghimera.corpus_types import CorpusReceipt
from ghimera.embedding import SelfHostedEncoder
from ghimera.human_browser_types import HumanAssistant
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_types import ResearchRequest
from ghimera.result_archive import (
    ArchiveReceipt,
    ArchiveReservation,
    ResearchResultArchive,
    bounded_file,
)
from ghimera.transport import Resolver

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CorpusId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Count = Annotated[int, Field(strict=True, ge=0)]


class CommandCorpusBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.command-corpus/1"] = Field(alias="schema")
    config_path: Path
    config_sha256: Digest
    mode: Literal["create", "open"]
    append_result: Annotated[bool, Field(strict=True)]
    corpus_id: CorpusId | None = Field(default=None, exclude_if=lambda value: value is None)
    generation: Count | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def bound(self) -> "CommandCorpusBinding":
        if not self.config_path.is_absolute():
            raise ValueError("corpus configuration requires an absolute file path")
        if (self.mode == "open") != (self.corpus_id is not None and self.generation is not None):
            raise ValueError("open requires the original corpus identity and generation")
        if self.mode == "create" and (
            not self.append_result or self.corpus_id is not None or self.generation is not None
        ):
            raise ValueError("create requires append and cannot claim an existing corpus")
        return self


class CorpusCommandOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.collector-command/7"] = Field(alias="schema")
    action: Literal["execute", "handoff"]
    command: CommandOptions
    corpus: CommandCorpusBinding

    @model_validator(mode="after")
    def reservation(self) -> "CorpusCommandOptions":
        execution = self.command.execution
        if execution is None or execution.operation not in {"run", "resume", "recover"}:
            raise ValueError("corpus command requires the native reserved-output execution route")
        if self.corpus.mode == "create" and (
            self.action != "execute" or execution.operation != "run"
        ):
            raise ValueError("corpus creation is restricted to a fresh collection")
        if self.action == "handoff" and (
            self.corpus.mode != "open"
            or not self.corpus.append_result
            or execution.operation != "run"
            or self.command.request_path is None
        ):
            raise ValueError("handoff requires open corpus and original run request")
        return self


class CorpusHandoffFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: Literal["refused", "invalid", "io", "unexpected"]
    refusal: RefusalCode | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def attributed(self) -> "CorpusHandoffFailure":
        if (self.category == "refused") != (self.refusal is not None):
            raise ValueError("native refusal attribution requires its bounded code")
        return self


class CorpusCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.corpus-command-result/1"] = Field(alias="schema")
    command: ArchiveReceipt | CheckpointReceipt
    corpus_config: CorpusConfig
    config_sha256: Digest
    corpus_id: CorpusId
    generation: Count
    handoff: Literal["not_requested", "pending", "complete", "failed"]
    appended: CorpusReceipt | None = Field(default=None, exclude_if=lambda value: value is None)
    failure: CorpusHandoffFailure | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def receipt(self) -> "CorpusCommandResult":
        if (self.handoff == "complete") != (self.appended is not None):
            raise ValueError("completed handoff requires its native corpus receipt")
        if (self.handoff == "failed") != (self.failure is not None):
            raise ValueError("failed handoff requires safe typed attribution")
        if (self.handoff == "pending" and not isinstance(self.command, CheckpointReceipt)) or (
            self.handoff in {"failed", "complete"} and not isinstance(self.command, ArchiveReceipt)
        ):
            raise ValueError("handoff phase differs from native output completion")
        if self.appended is not None and (
            self.appended.corpus_id != self.corpus_id
            or self.appended.generation != self.generation
            or self.appended.config_sha256 != self.corpus_config.identity
        ):
            raise ValueError("handoff receipt differs from owning corpus")
        return self


async def execute_corpus(
    options: CorpusCommandOptions,
    *,
    source_resolver: Resolver | None = None,
    human_assistant: HumanAssistant | None = None,
) -> CorpusCommandResult:
    """Own configured corpus lifetime; collect once, then hand off acknowledged originals."""
    options = CorpusCommandOptions.model_validate(options.model_dump())
    command, binding = options.command, options.corpus
    raw = bounded_file(binding.config_path, command.max_input_bytes)
    if hashlib.sha256(raw).hexdigest() != binding.config_sha256:
        raise ValueError("corpus configuration file changed")
    policy = CorpusConfig.model_validate(tomllib.loads(raw.decode()))
    config = GhimeraConfig.model_validate(
        tomllib.loads(bounded_file(command.config_path, command.max_input_bytes).decode())
    )
    if config.journal is None:
        raise ValueError("corpus command requires a sealed native journal")
    credentials = (
        CredentialBindings.model_validate_json(
            bounded_file(command.bindings_path, command.max_input_bytes)
        )
        if command.bindings_path is not None
        else CredentialBindings(schema="chimera.command-credentials/1")
    ).resolve()
    from ghimera.corpus_search_config import CorpusSearchConfig

    uses_corpus = isinstance(config.search, CorpusSearchConfig) or (
        config.research is not None and config.research.retained_evidence is not None
    )
    if binding.mode == "create" and uses_corpus:
        raise ValueError("a fresh corpus cannot claim pre-existing discovery/retained identities")
    corpus = EvidenceCorpus(
        policy,
        encoder=SelfHostedEncoder(policy.encoder, credential=credentials.encoder),
        query_encoder=SelfHostedEncoder(policy.query_encoder, credential=credentials.encoder),
        create=binding.mode == "create",
    )
    try:
        receipt: ArchiveReceipt | CheckpointReceipt
        admitted, corpus_id, generation = corpus.admit_command()
        if admitted != policy:
            raise ValueError("corpus admission changed its full configuration")
        if binding.mode == "open" and (corpus_id, generation) != (
            binding.corpus_id,
            binding.generation,
        ):
            raise ValueError("corpus identity or original generation changed")
        # Handoff has no Collector factory: use the same owning reader guards here too.
        if isinstance(config.search, CorpusSearchConfig):
            from ghimera.corpus_search import CorpusLeadSearch

            CorpusLeadSearch(
                config.search,
                corpus,
                run_policy=config.research.reranking if config.research is not None else None,
            )
        if config.research is not None and config.research.retained_evidence is not None:
            from ghimera.corpus_evidence import CorpusEvidenceReader

            CorpusEvidenceReader(config.research.retained_evidence.reader, corpus)
        if options.action == "handoff":
            if command.request_path is None:
                raise ValueError("handoff lost its original request")
            request = ResearchRequest.model_validate_json(
                bounded_file(command.request_path, command.max_input_bytes)
            )
            receipt, result = await ResearchResultArchive.acknowledged_research(
                command.output_directory,
                reservation=ArchiveReservation(
                    schema="ghimera.command-output/1",
                    run_id=command.run_id,
                    config_sha256=hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
                    request_sha256=request.content_digest(),
                ),
                config=config,
                request=request,
                max_bytes=command.max_result_bytes,
                max_reservation_bytes=command.max_input_bytes,
            )
        else:
            outcome = await execute(
                command,
                source_resolver=source_resolver,
                human_assistant=human_assistant,
                corpus=corpus if uses_corpus else None,
            )
            if not isinstance(outcome, (ArchiveReceipt, CheckpointReceipt)):
                raise ValueError("corpus composition received an unsupported caller result")
            receipt = outcome
            result = None
            if isinstance(outcome, ArchiveReceipt) and binding.append_result:
                # Execute already proved the reservation/request; read back its complete seal.
                reservation = ArchiveReservation.model_validate_json(
                    bounded_file(
                        command.output_directory / "reservation.json", command.max_input_bytes
                    )
                )
                if (
                    reservation.run_id != command.run_id
                    or reservation.config_sha256
                    != hashlib.sha256(config.model_dump_json().encode()).hexdigest()
                ):
                    raise ValueError("archive reservation changed before handoff")
                sealed, result = ResearchResultArchive.acknowledged(
                    command.output_directory,
                    reservation=reservation,
                    max_bytes=command.max_result_bytes,
                    max_reservation_bytes=command.max_input_bytes,
                )
                if sealed != outcome:
                    raise ValueError("archive changed before corpus handoff")
        handoff: Literal["not_requested", "pending", "complete", "failed"] = "not_requested"
        appended = None
        failure = None
        if binding.append_result:
            handoff = "pending"
            if result is not None:
                try:
                    appended = await corpus.append(result.harvest)
                    generation = appended.generation
                    handoff = "complete"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Collection is complete. Retain its receipt; never turn this into recrawl.
                    handoff = "failed"
                    failure = (
                        CorpusHandoffFailure(category="refused", refusal=exc.code)
                        if isinstance(exc, GhimeraRefused)
                        else CorpusHandoffFailure(
                            category="invalid"
                            if isinstance(exc, ValueError)
                            else "io"
                            if isinstance(exc, OSError)
                            else "unexpected"
                        )
                    )
        return CorpusCommandResult(
            schema="ghimera.corpus-command-result/1",
            command=receipt,
            corpus_config=policy,
            config_sha256=binding.config_sha256,
            corpus_id=corpus_id,
            generation=generation,
            handoff=handoff,
            appended=appended,
            failure=failure,
        )
    finally:
        corpus.close()
