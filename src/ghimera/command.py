"""Intent command: explicit recipe, secret bindings, and private complete-result output."""

import argparse
import asyncio
import hashlib
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ghimera.collector import Collector
from ghimera.config import GhimeraConfig
from ghimera.continuation import CheckpointReceipt, CheckpointStore, ResearchSuspended
from ghimera.corpus import EvidenceCorpus
from ghimera.embedding_types import EmbeddingReferences
from ghimera.human_browser_types import HumanAssistant
from ghimera.model_reconciliation_types import (
    ModelAttemptAuthorization,
    ModelReconciliationDecision,
    ModelUnknownObservation,
)
from ghimera.refusals import GhimeraRefused
from ghimera.research_recovery_store import ResearchRecoveryStore
from ghimera.research_types import ResearchRequest
from ghimera.result_archive import (
    ArchiveReceipt,
    ArchiveReservation,
    ResearchResultArchive,
    bounded_file,
)
from ghimera.source_sessions import SourceCredentials
from ghimera.source_work import SourceWorkFailure, SourceWorkStore
from ghimera.terminal_assistance import TerminalAssistanceConfig, TerminalHumanAssistant
from ghimera.transport import Resolver

Positive = Annotated[int, Field(strict=True, gt=0)]
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class CommandExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal[
        "ghimera.command-execution/1",
        "ghimera.command-execution/2",
        "ghimera.command-execution/3",
        "ghimera.command-execution/4",
    ] = Field(alias="schema")
    operation: Literal["run", "resume", "recover", "observe_model_unknown", "reconcile_model"]
    checkpoint_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    suspend_after_rounds: Positive | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    recovery_boundary: Literal["source_completion"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_decision: ModelReconciliationDecision | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_attempt: ModelAttemptAuthorization | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def checkpoint_binding(self) -> "CommandExecution":
        if self.schema_version == "ghimera.command-execution/4":
            if (
                self.operation not in {"observe_model_unknown", "reconcile_model", "recover"}
                or self.snapshot_sha256 is None
                or self.checkpoint_sha256 is not None
                or self.suspend_after_rounds is not None
                or self.recovery_boundary is not None
                or (self.operation == "reconcile_model") != (self.model_decision is not None)
                or self.model_attempt is not None
                and self.operation != "recover"
            ):
                raise ValueError(
                    "model caller route requires its explicit operation and exact snapshot"
                )
            if (
                self.model_decision is not None
                and self.model_decision.observed.snapshot_sha256 != self.snapshot_sha256
            ):
                raise ValueError("decision differs from command snapshot")
            return self
        if (
            self.model_decision is not None
            or self.model_attempt is not None
            or self.operation in {"observe_model_unknown", "reconcile_model"}
        ):
            raise ValueError("model reconciliation requires command execution /4")
        if (self.operation == "resume") != (self.checkpoint_sha256 is not None):
            raise ValueError("only resume requires an explicit checkpoint digest")
        recovering = self.operation == "recover"
        if recovering != (
            self.schema_version in {"ghimera.command-execution/2", "ghimera.command-execution/3"}
        ) or recovering != (self.snapshot_sha256 is not None):
            raise ValueError("recovery requires execution /2 and its exact snapshot digest")
        if (self.schema_version == "ghimera.command-execution/3") != (
            self.recovery_boundary is not None
        ):
            raise ValueError("source completion requires execution /3 and explicit boundary")
        if recovering and self.suspend_after_rounds is not None:
            raise ValueError("model-boundary recovery cannot introduce a new suspension policy")
        return self


class CommandOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal[
        "chimera.collector-command/1",
        "ghimera.collector-command/2",
        "ghimera.collector-command/3",
        "ghimera.collector-command/4",
        "ghimera.collector-command/5",
    ] = Field(alias="schema")
    config_path: Path
    request_path: Path | None = Field(default=None, exclude_if=lambda value: value is None)
    output_directory: Path
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    max_input_bytes: Positive
    max_result_bytes: Positive
    bindings_path: Path | None = None
    references_path: Path | None = None
    execution: CommandExecution | None = Field(default=None, exclude_if=lambda value: value is None)
    human_assistance: TerminalAssistanceConfig | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def paths(self) -> "CommandOptions":
        caller_route = (
            self.execution is not None
            and self.execution.schema_version == "ghimera.command-execution/4"
        )
        recovering = self.execution is not None and self.execution.operation in {
            "recover",
            "observe_model_unknown",
            "reconcile_model",
        }
        if self.schema_version == "ghimera.collector-command/5":
            if not caller_route:
                raise ValueError("command /5 requires explicit model caller route")
        elif caller_route:
            raise ValueError("model caller route requires command /5")
        elif self.schema_version == "ghimera.collector-command/4":
            if not recovering:
                raise ValueError("command /4 requires explicit recovery")
        elif recovering:
            raise ValueError("model-boundary recovery requires command /4")
        elif self.schema_version == "ghimera.collector-command/3":
            if self.human_assistance is None:
                raise ValueError("command /3 requires explicit human assistance")
        elif self.human_assistance is not None:
            raise ValueError("human assistance requires command /3")
        elif (self.schema_version == "ghimera.collector-command/2") != (self.execution is not None):
            raise ValueError("command /2 requires an execution policy; legacy /1 forbids it")
        resuming = self.execution is not None and self.execution.operation != "run"
        if resuming == (self.request_path is not None):
            raise ValueError("run needs a request file; resume uses its pinned original request")
        for path in (
            self.config_path,
            self.request_path,
            self.output_directory,
            self.bindings_path,
            self.references_path,
        ):
            if path is not None and not path.is_absolute():
                raise ValueError("command paths must be absolute")
        return self


class CompletionCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint: Annotated[str, Field(min_length=1)]
    environment_variable: EnvName


class HeaderCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Annotated[str, Field(min_length=1)]
    environment_variable: EnvName


class SourceCredentialBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: Annotated[str, Field(min_length=1)]
    headers: Annotated[tuple[HeaderCredential, ...], Field(min_length=1)]


@dataclass(frozen=True)
class ResolvedCredentials:
    models: Mapping[str, SecretStr] = field(repr=False)
    encoder: SecretStr | None = field(repr=False)
    sources: Mapping[str, SourceCredentials] = field(repr=False)

    def completion_roles(
        self, config: GhimeraConfig
    ) -> tuple[Mapping[str, SecretStr], SecretStr | None, SecretStr | None]:
        services = config.models
        text_endpoints = (
            {
                service.endpoint
                for service in (
                    services.planner,
                    services.analyst,
                    services.reviewer,
                    services.judge,
                )
            }
            if services is not None
            else set()
        )
        visual = config.visuals
        vision = visual.vision if visual is not None else None
        reviewer = visual.reviewer if visual is not None else None
        visual_endpoints = {
            service.endpoint for service in (vision, reviewer) if service is not None
        }
        if not set(self.models) <= text_endpoints | visual_endpoints:
            raise ValueError("completion credentials require an explicit recipe endpoint")
        return (
            {
                endpoint: secret
                for endpoint, secret in self.models.items()
                if endpoint in text_endpoints
            },
            self.models.get(vision.endpoint) if vision is not None else None,
            self.models.get(reviewer.endpoint) if reviewer is not None else None,
        )


class CredentialBindings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["chimera.command-credentials/1"] = Field(alias="schema")
    completions: tuple[CompletionCredential, ...] = ()
    encoder_environment_variable: EnvName | None = None
    sources: tuple[SourceCredentialBinding, ...] = ()

    @model_validator(mode="after")
    def distinct(self) -> "CredentialBindings":
        if len({entry.endpoint for entry in self.completions}) != len(self.completions) or len(
            {entry.session_id for entry in self.sources}
        ) != len(self.sources):
            raise ValueError("credential bindings cannot duplicate endpoints or sessions")
        return self

    def resolve(self, environment: Mapping[str, str] | None = None) -> ResolvedCredentials:
        values = os.environ if environment is None else environment

        def secret(name: str) -> SecretStr:
            value = values.get(name)
            if not value or any(ord(char) < 32 or ord(char) > 126 for char in value):
                raise ValueError("a declared credential is missing or invalid")
            return SecretStr(value)

        return ResolvedCredentials(
            models={
                entry.endpoint: secret(entry.environment_variable) for entry in self.completions
            },
            encoder=secret(self.encoder_environment_variable)
            if self.encoder_environment_variable
            else None,
            sources={
                entry.session_id: SourceCredentials(
                    headers=tuple(
                        (header.name, secret(header.environment_variable))
                        for header in entry.headers
                    )
                )
                for entry in self.sources
            },
        )


def assemble_collector(
    config: GhimeraConfig,
    bindings: ResolvedCredentials,
    references: EmbeddingReferences | None,
    *,
    source_resolver: Resolver | None = None,
    human_assistant: HumanAssistant | None = None,
    corpus: EvidenceCorpus | None = None,
) -> Collector:
    """Native dependency admission shared by commands and unattended startup."""
    model_credentials, vision_credential, visual_reviewer_credential = bindings.completion_roles(
        config
    )
    return Collector(
        config,
        references=references,
        model_credentials=model_credentials,
        vision_credential=vision_credential,
        visual_reviewer_credential=visual_reviewer_credential,
        encoder_credential=bindings.encoder,
        source_credentials=bindings.sources,
        source_resolver=source_resolver,
        human_assistant=human_assistant,
        corpus=corpus,
    )


async def execute(
    options: CommandOptions,
    *,
    source_resolver: Resolver | None = None,
    human_assistant: HumanAssistant | None = None,
    corpus: EvidenceCorpus | None = None,
) -> ArchiveReceipt | CheckpointReceipt | ModelUnknownObservation | ModelAttemptAuthorization:
    options = CommandOptions.model_validate(options.model_dump())
    config = GhimeraConfig.model_validate(
        tomllib.loads(bounded_file(options.config_path, options.max_input_bytes).decode())
    )
    if options.human_assistance is not None:
        if config.human_browser is None:
            raise ValueError("terminal assistance requires the recipe's explicit browser binding")
        if human_assistant is None:
            human_assistant = TerminalHumanAssistant.from_standard_streams(options.human_assistance)
    elif human_assistant is not None:
        raise ValueError("human assistance requires an explicit command policy")
    execution = options.execution
    if (
        execution is not None
        and execution.operation in {"run", "resume"}
        and config.continuation is None
    ):
        raise ValueError("command /2 requires the recipe's durable continuation policy")
    if execution is not None and execution.operation in {
        "recover",
        "observe_model_unknown",
        "reconcile_model",
    }:
        if config.research_recovery is None or execution.snapshot_sha256 is None:
            raise ValueError("recovery requires the recipe's explicit policy and snapshot digest")
        request = (
            SourceWorkStore.completion(
                config, options.run_id, execution.snapshot_sha256
            ).snapshot.request
            if execution.recovery_boundary == "source_completion"
            else (
                ResearchRecoveryStore(config, options.run_id, config.research_recovery)
                .read(
                    execution.snapshot_sha256,
                    decision=execution.model_decision,
                    attempt=execution.model_attempt,
                    observe_unknown=execution.operation == "observe_model_unknown",
                )
                .snapshot.request
            )
        )
    elif execution is not None and execution.operation == "resume":
        if execution.checkpoint_sha256 is None:
            raise ValueError("resume requires its checkpoint digest")
        request = CheckpointStore(config, options.run_id).read(execution.checkpoint_sha256).request
    else:
        if options.request_path is None:
            raise ValueError("run requires a request file")
        request = ResearchRequest.model_validate_json(
            bounded_file(options.request_path, options.max_input_bytes)
        )
    bindings = (
        CredentialBindings.model_validate_json(
            bounded_file(options.bindings_path, options.max_input_bytes)
        )
        if options.bindings_path is not None
        else CredentialBindings(schema="chimera.command-credentials/1")
    ).resolve()
    references = (
        EmbeddingReferences.model_validate_json(
            bounded_file(options.references_path, options.max_input_bytes)
        )
        if options.references_path is not None
        else None
    )
    collector = assemble_collector(
        config,
        bindings,
        references,
        source_resolver=source_resolver,
        human_assistant=human_assistant,
        corpus=corpus,
    )
    request = collector.validate_request(request)
    # Reserve output before any paid/discovery work; an existing result is not reusable.
    if execution is None:
        archive = ResearchResultArchive.create(options.output_directory, run_id=options.run_id)
    else:
        reservation = ArchiveReservation(
            schema="ghimera.command-output/1",
            run_id=options.run_id,
            config_sha256=hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
            request_sha256=request.content_digest(),
        )
        if execution.operation != "run":
            archive = ResearchResultArchive.resume(
                options.output_directory, reservation=reservation, max_bytes=options.max_input_bytes
            )
        else:
            archive = ResearchResultArchive.reserve(
                options.output_directory, reservation=reservation, max_bytes=options.max_input_bytes
            )
    try:
        if execution is not None and execution.operation == "observe_model_unknown":
            if execution.snapshot_sha256 is None:
                raise ValueError("observation requires its snapshot pin")
            return collector.observe_model_unknown(
                options.run_id, snapshot_sha256=execution.snapshot_sha256
            )
        if execution is not None and execution.operation == "reconcile_model":
            if (
                execution.model_decision is None
                or execution.model_decision.observed.run_id != options.run_id
            ):
                raise ValueError("decision changed its original run")
            return await collector.reconcile_model(execution.model_decision)
        if execution is not None and execution.operation == "recover":
            if execution.snapshot_sha256 is None:
                raise ValueError("recovery requires its exact snapshot digest")
            result = await collector.recover(
                options.run_id,
                snapshot_sha256=execution.snapshot_sha256,
                boundary=execution.recovery_boundary or "model_return",
                attempt=execution.model_attempt,
            )
        elif execution is not None and execution.operation == "resume":
            if execution.checkpoint_sha256 is None:
                raise ValueError("resume requires its checkpoint digest")
            result = await collector.resume(
                options.run_id,
                checkpoint_sha256=execution.checkpoint_sha256,
                suspend_after_rounds=execution.suspend_after_rounds,
            )
        else:
            result = await collector.run(
                request,
                run_id=options.run_id,
                suspend_after_rounds=execution.suspend_after_rounds
                if execution is not None
                else None,
            )
        return archive.write(result, max_bytes=options.max_result_bytes)
    except ResearchSuspended as paused:
        return paused.receipt
    finally:
        archive.close()


class _CommandParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse normally echoes unknown arguments and invalid values verbatim.
        self.exit(2, "command_arguments_invalid\n")


def main(argv: list[str] | None = None) -> int:
    parser = _CommandParser(
        description="Run configured intent research and retain its full evidence."
    )
    parser.add_argument("--job", required=True, type=Path, help="versioned collector-command TOML")
    parser.add_argument(
        "--max-job-bytes", required=True, type=int, help="explicit bound before parsing the job"
    )
    args = parser.parse_args(argv)
    try:
        options = CommandOptions.model_validate(
            tomllib.loads(bounded_file(args.job, args.max_job_bytes).decode())
        )
        receipt = asyncio.run(execute(options))
    except SourceWorkFailure:
        sys.stderr.write("command_source_work_failed\n")
        return 2
    except GhimeraRefused as exc:
        sys.stderr.write("command_refused:" + exc.code.value + "\n")
        return 2
    except ValueError:
        # Exception messages can contain untrusted input or credentials. Never echo them.
        sys.stderr.write("command_input_invalid\n")
        return 2
    except OSError:
        sys.stderr.write("command_io_failed\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("command_interrupted\n")
        return 130
    sys.stdout.write(receipt.model_dump_json() + "\n")
    if isinstance(receipt, CheckpointReceipt):
        return 3
    if isinstance(receipt, (ModelUnknownObservation, ModelAttemptAuthorization)):
        return 0
    return 0 if receipt.status == "answered" else 1
