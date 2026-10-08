"""Existing sealed-output admission never repairs or recreates uncertain bytes."""

import hashlib

import pytest

from ghimera.result_archive import ArchiveReservation, ResearchResultArchive
from tests.test_intent_research import run


def sealed(tmp_path):
    result, _, _ = run()
    reservation = ArchiveReservation(
        schema="ghimera.command-output/1",
        run_id="original",
        config_sha256=hashlib.sha256(
            result.harvest.receipt.effective_config.model_dump_json().encode()
        ).hexdigest(),
        request_sha256="a" * 64,
    )
    path = tmp_path / "output"
    archive = ResearchResultArchive.reserve(path, reservation=reservation, max_bytes=1000000)
    receipt = archive.write(result, max_bytes=10000000)
    return path, archive, reservation, receipt, result


def test_acknowledged_requires_native_writer_lock_and_preserves_originals(tmp_path):
    path, writer, reservation, receipt, result = sealed(tmp_path)
    before = {item.name: item.read_bytes() for item in path.iterdir()}
    try:
        with pytest.raises(BlockingIOError):
            ResearchResultArchive.acknowledged(
                path, reservation=reservation, max_bytes=10000000, max_reservation_bytes=1000000
            )
    finally:
        writer.close()
    assert ResearchResultArchive.acknowledged(
        path, reservation=reservation, max_bytes=10000000, max_reservation_bytes=1000000
    ) == (receipt, result)
    assert before == {item.name: item.read_bytes() for item in path.iterdir()}


@pytest.mark.parametrize("change", ("request", "partial", "extra", "result", "bound"))
def test_acknowledged_holds_mismatch_or_uncertain_output_without_mutation(tmp_path, change):
    path, writer, reservation, _, _ = sealed(tmp_path)
    writer.close()
    max_bytes = 10000000
    if change == "request":
        reservation = ArchiveReservation.model_validate(
            {**reservation.model_dump(), "request_sha256": "b" * 64}
        )
    elif change == "partial":
        (path / "receipt.json").unlink()
    elif change == "extra":
        (path / ".result.json.pending").write_bytes(b"uncertain")
    elif change == "result":
        (path / "result.json").write_bytes(b"{}")
    else:
        max_bytes = 1
    before = {item.name: item.read_bytes() for item in path.iterdir()}
    with pytest.raises(ValueError):
        ResearchResultArchive.acknowledged(
            path, reservation=reservation, max_bytes=max_bytes, max_reservation_bytes=1000000
        )
    assert before == {item.name: item.read_bytes() for item in path.iterdir()}
