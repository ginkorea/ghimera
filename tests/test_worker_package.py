"""Installed-parent collision and exact owned package lifetime contracts."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from ghimera import passive_worker, worker_package
from ghimera.passive_worker import PassiveWorker
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.worker_package import admit_package


def package(tmp_path, body):
    root = tmp_path / "caller-site-packages"
    owner = root / "ghimera"
    owner.mkdir(parents=True)
    (owner / "__init__.py").write_text("")
    (owner / "probe.py").write_text(body)
    (root / "json.py").write_text("raise RuntimeError('parent dependency leaked')")
    facade = root / "chimera"
    facade.mkdir()
    (facade / "__init__.py").write_text("from ghimera import *")
    return owner


def worker(tmp_path, **updates):
    values = dict(
        interpreter=Path(sys.executable),
        module="ghimera.probe",
        work_directory=tmp_path / "worker",
        max_workers=1,
        timeout_seconds=5.0,
        max_output_bytes=8192,
        max_diagnostic_bytes=8192,
        cleanup_timeout_seconds=2.0,
        environment={"PYTHONPATH": str(tmp_path / "caller-site-packages")},
    )
    values.update(updates)
    return PassiveWorker(**values)


def bind(monkeypatch, owner):
    async def admitted():
        return await admit_package(owner)

    monkeypatch.setattr(passive_worker, "admit_package", admitted)


def test_installed_parent_collision_uses_only_package_and_worker_dependency(tmp_path, monkeypatch):
    owner = package(
        tmp_path,
        "import json, os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['PYTHONPATH'])\n"
        "print(json.dumps({'root': str(root), 'dependency': json.__file__, "
        "'package': __file__, 'entries': sorted(p.name for p in root.iterdir())}))\n",
    )
    bind(monkeypatch, owner)
    result = json.loads(asyncio.run(worker(tmp_path).run(b"{}")))
    assert result["entries"] == ["chimera", "ghimera"]
    assert "caller-site-packages" not in result["dependency"]
    assert result["package"].startswith(result["root"])
    assert not Path(result["root"]).exists()
    assert list((tmp_path / "worker").iterdir()) == []
    assert (owner.parent / "json.py").exists()


def test_admission_freezes_exact_bytes_and_excludes_cache_and_neighbor(tmp_path):
    owner = package(tmp_path, "print('original')\n")
    (owner / "__pycache__").mkdir()
    (owner / "__pycache__" / "probe.pyc").write_bytes(b"not code")
    (owner / "state.json").write_text('"not library code"')

    async def exercise():
        inventory = await admit_package(owner)
        before = inventory.sha256
        (owner / "probe.py").write_text("print('changed')\n")
        async with inventory.project(tmp_path) as root:
            assert (root / "ghimera" / "probe.py").read_text() == "print('original')\n"
            assert not (root / "json.py").exists()
            assert not (root / "ghimera" / "state.json").exists()
            assert not (root / "ghimera" / "__pycache__").exists()
            assert inventory.sha256 == before
            assert inventory.size_bytes == sum(len(item.body) for item in inventory.files)
            assert all(
                hashlib.sha256(item.body).hexdigest() == item.sha256 for item in inventory.files
            )
            assert all(
                (root.joinpath(*item.path.parts).stat().st_mode & 0o222) == 0
                for item in inventory.files
            )
        assert not root.exists()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["file", "directory", "facade"])
def test_links_refused_before_contact(tmp_path, monkeypatch, kind):
    owner = package(tmp_path, "raise RuntimeError('must never contact')\n")
    if kind == "file":
        (owner / "linked.py").symlink_to(owner / "probe.py")
    elif kind == "directory":
        (owner / "linked").symlink_to(owner, target_is_directory=True)
    else:
        (owner.parent / "chimera" / "__init__.py").unlink()
        (owner.parent / "chimera").rmdir()
        (owner.parent / "chimera").symlink_to(owner, target_is_directory=True)
    bind(monkeypatch, owner)
    with pytest.raises(GhimeraRefused) as exc:
        asyncio.run(worker(tmp_path).run(b"{}"))
    assert exc.value.code == RefusalCode.ADAPTER_CONTRACT
    assert list((tmp_path / "worker").iterdir()) == []


def test_changed_inventory_size_refused_before_contact(tmp_path, monkeypatch):
    owner = package(tmp_path, "print('must not run')\n")
    checkpoint = asyncio.sleep
    calls = 0

    async def drift(delay):
        nonlocal calls
        calls += 1
        if calls == 2:
            (owner / "probe.py").write_text("print('changed after enumeration')\n")
        await checkpoint(delay)

    monkeypatch.setattr(worker_package.asyncio, "sleep", drift)
    bind(monkeypatch, owner)
    with pytest.raises(GhimeraRefused) as exc:
        asyncio.run(worker(tmp_path).run(b"{}"))
    assert exc.value.code == RefusalCode.ADAPTER_CONTRACT
    assert list((tmp_path / "worker").iterdir()) == []


def test_concurrent_calls_have_distinct_projections_with_shared_slot(tmp_path, monkeypatch):
    owner = package(
        tmp_path,
        "import json,os,pathlib,time\n"
        "root = pathlib.Path(os.environ['PYTHONPATH'])\n"
        "assert len(list(root.parent.glob('package-*'))) == 1\n"
        "time.sleep(0.01)\n"
        "print(json.dumps(str(root)))\n",
    )
    bind(monkeypatch, owner)

    async def exercise():
        client = worker(tmp_path)
        return await asyncio.gather(client.run(b"{}"), client.run(b"{}"))

    roots = [json.loads(result) for result in asyncio.run(exercise())]
    assert roots[0] != roots[1]
    assert all(not Path(root).exists() for root in roots)
    assert list((tmp_path / "worker").iterdir()) == []


@pytest.mark.parametrize("ending", ["failure", "timeout", "overflow", "cancel"])
def test_child_is_reaped_before_exact_projection_cleanup(tmp_path, monkeypatch, ending):
    marker = tmp_path / "pid"
    owner = package(
        tmp_path,
        "import os, pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        + {
            "failure": "raise RuntimeError('controlled failure')\n",
            "timeout": "time.sleep(30)\n",
            "overflow": "print('x' * 10000)\n",
            "cancel": "time.sleep(30)\n",
        }[ending],
    )
    bind(monkeypatch, owner)

    async def exercise():
        client = worker(tmp_path, timeout_seconds=1.0 if ending == "timeout" else 5.0)
        task = asyncio.create_task(client.run(b"{}"))
        if ending == "cancel":
            async with asyncio.timeout(2):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if ending == "cancel" else GhimeraRefused):
            await task

    asyncio.run(exercise())
    assert list((tmp_path / "worker").iterdir()) == []
    # Unlike a pre-contact deadline refusal, this witness requires a real child.
    assert marker.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
