"""0.4.12: dotted run ids, refusal detail sentences and per-text cuts instead of refusals."""

import asyncio
import os

import pytest

from ghimera.budget import RunBudget
from ghimera.identifiers import valid_run_id
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.ledger import Ledger
from ghimera.models import Extracted, Goal, LedgerRow, LinkCandidate, ModelIdentity
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_scoring import EmbeddingScorer, link_input, selected_windows
from tests.test_embedding_scoring import endpoint as endpoint  # noqa: F401 - fixture
from tests.test_embedding_scoring import policy, references, run_config, service
from tests.test_run_journal import configured

JUDGE = ModelIdentity(model_id="judge", revision="1", location="test_double")


@pytest.mark.parametrize(
    "run_id", ["ghimera.0123456789abcdef01234567-r1", "a.b", "run-1", "R_2.v3"]
)
def test_dotted_run_ids_are_journal_identities(tmp_path, run_id):
    assert valid_run_id(run_id)
    cfg = configured(tmp_path)
    sink = DirectoryLedgerSink(cfg, run_id, Goal(text="ports"), JUDGE)
    sink.append(LedgerRow(sequence=0, event="policy", reason="fixture"))
    assert read_journal(cfg.journal, run_id).rows[0].reason == "fixture"
    assert (tmp_path / "runs" / run_id / "ledger.jsonl").is_file()


@pytest.mark.parametrize("run_id", [".hidden", "..", "a/b", "", "-x", "a b", "ä"])
def test_invalid_run_ids_refuse_with_the_alphabet_named(tmp_path, run_id):
    assert not valid_run_id(run_id)
    with pytest.raises(GhimeraRefused) as refused:
        DirectoryLedgerSink(configured(tmp_path), run_id, Goal(text="ports"), JUDGE)
    assert refused.value.code == RefusalCode.LEDGER_SINK_FAILED
    assert "journal alphabet" in refused.value.detail
    assert "'.', '_' or '-'" in refused.value.detail


def test_journal_directory_mode_refusal_says_which_check(tmp_path):
    cfg = configured(tmp_path)
    (tmp_path / "runs").mkdir(mode=0o755)
    os.chmod(tmp_path / "runs", 0o755)
    with pytest.raises(GhimeraRefused) as refused:
        DirectoryLedgerSink(cfg, "run.1", Goal(text="ports"), JUDGE)
    assert refused.value.code == RefusalCode.LEDGER_SINK_FAILED
    assert refused.value.detail == (
        "journal directory runs has mode 755; group/other access must be 0"
    )
    assert "(journal directory runs has mode 755" in str(refused.value)


def test_every_refusal_carries_a_detail_even_without_a_sentence():
    def check() -> None:
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)

    with pytest.raises(GhimeraRefused) as refused:
        check()
    detail = refused.value.detail
    assert detail.startswith("refused by ") and "test_refusal_detail.check line " in detail
    long = GhimeraRefused(RefusalCode.FETCH_FAILED, "x\n" * 1000)
    assert len(long.detail) == 400 and "\n" not in long.detail


def test_link_text_is_cut_to_the_encoder_bound_not_refused():
    cfg = service(1, max_text_chars=60)
    scoring = policy(cfg, references(cfg), window_chars=50, overlap_chars=5)
    url = "https://example.org/a/very/long/path/segment?query=" + "q" * 200 + "#frag"
    text, cut = link_input(LinkCandidate(url=url, anchor="ports " * 10), scoring)
    assert len(text) == 60 and cut == len(url) + 1 + 30 - 60
    head, separator, anchor = text.partition("\n")
    assert separator and head == "https://example.org/a/very/long/path/segment"
    assert anchor == ("ports " * 10)[: 60 - 1 - len(head)]
    short, zero = link_input(LinkCandidate(url="https://example.org/x", anchor="a"), scoring)
    assert (short, zero) == ("https://example.org/x\na", 0)


def test_whitespace_windows_are_skipped_not_refused():
    cfg = service(1)
    scoring = policy(cfg, references(cfg), window_chars=20, overlap_chars=0, max_windows=5)
    text = "ports evidence here." + " " * 60 + "more ports text"
    spans = selected_windows(text, Goal(text="ports"), scoring)
    assert spans and all(text[start:end].strip() for start, end in spans)


def test_overlong_link_scores_the_page_with_cut_recorded(endpoint):  # noqa: F811
    service_cfg = service(endpoint[0], max_text_chars=120)
    refs = references(service_cfg)
    scoring = policy(service_cfg, refs, max_windows=1)
    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    long = "https://example.org/ports/" + "p" * 300
    document = Extracted(
        title="native",
        text="ports evidence " * 5,
        language="en",
        links=(LinkCandidate(url=long, anchor="ports"),),
    )
    ranked = asyncio.run(
        EmbeddingScorer(scoring, _encoder(service_cfg), refs).score(
            Goal(text="ports"), document, budget, ledger
        )
    )
    assert [link.url for link in ranked] == [long]
    similarity = ledger.snapshot()[-1].similarity
    assert similarity is not None and similarity.links[0].omitted_chars > 0
    assert similarity.links[0].url == long


def _encoder(cfg):
    from ghimera.embedding import SelfHostedEncoder

    return SelfHostedEncoder(cfg)
