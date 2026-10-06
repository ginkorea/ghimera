"""Native Tor HTTP contracts: real curl through an owned SOCKS server, no DNS escape."""

import asyncio
import base64
import hashlib
import socket
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from chimera.budget import RunBudget
from chimera.config import ChimeraConfig
from chimera.fetch import FetchLadder
from chimera.http import CurlRoute
from chimera.ledger import Ledger
from chimera.models import Scope
from chimera.refusals import ChimeraRefused
from chimera.transport import is_onion_v3
from chimera.transport_types import TransportConfig
from tests.test_http_fetch import site as site
from tests.test_http_fetch import state


class NoLocalDNS:
    async def resolve(self, host, port):
        raise AssertionError("Tor source DNS must never reach the local resolver")


def onion():
    public_key = bytes(range(32))
    checksum = hashlib.sha3_256(b".onion checksum" + public_key + b"\x03").digest()[:2]
    return base64.b32encode(public_key + checksum + b"\x03").decode().lower() + ".onion"


def policy(port, **updates):
    raw = {
        "schema": "chimera.transport/1",
        "default_route": "tor",
        "rules": [],
        "allow_cross_network_redirects": False,
        "allow_route_change_redirects": False,
        "tor": {
            "schema": "chimera.tor/1",
            "proxy_endpoint": f"socks5h://127.0.0.1:{port}",
            "bridge_directory": "/tmp",
            "connect_timeout_seconds": 2.0,
            "allow_open_web": True,
            "allow_onion": True,
            "allowed_ports": [80, 443],
            "isolation": "request",
        },
    }
    raw.update(updates)
    return TransportConfig.model_validate(raw)


async def socks_server(http_port, seen, *, resolved="127.0.0.1", auth_method=2):
    async def handle(reader, writer):
        upstream = None
        try:
            version, nmethods = await reader.readexactly(2)
            methods = await reader.readexactly(nmethods)
            assert version == 5 and methods == b"\x02"
            writer.write(bytes((5, auth_method)))
            await writer.drain()
            if auth_method != 2:
                return
            version, size = await reader.readexactly(2)
            username = await reader.readexactly(size)
            password = await reader.readexactly((await reader.readexactly(1))[0])
            assert version == 1 and username == b"<torS0X>0"
            assert password
            writer.write(b"\x01\x00")
            await writer.drain()
            version, command, reserved, kind = await reader.readexactly(4)
            if kind == 3:
                host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
            elif kind == 1:
                host = socket.inet_ntop(socket.AF_INET, await reader.readexactly(4))
            elif kind == 4:
                host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
            else:
                raise AssertionError("invalid address kind")
            port = int.from_bytes(await reader.readexactly(2), "big")
            seen.append((command, host, port, hashlib.sha256(password).hexdigest()))
            if command == 0xF0:
                writer.write(b"\x05\x00\x00\x01" + socket.inet_aton(resolved) + b"\x00\x00")
                await writer.drain()
                return
            assert command == 1 and host in {resolved, onion()}
            upstream_reader, upstream = await asyncio.open_connection("127.0.0.1", http_port)
            writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            await writer.drain()

            async def relay(source, target):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()

            tasks = {
                asyncio.create_task(relay(reader, upstream)),
                asyncio.create_task(relay(upstream_reader, writer)),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            if upstream:
                upstream.close()
                await upstream.wait_closed()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


def test_transport_config_and_onion_address_fail_closed():
    example = Path(__file__).parents[1] / "examples" / "chimera-tor.toml"
    assert ChimeraConfig.from_toml(example).transport.default_route == "tor"
    assert is_onion_v3(onion()) and is_onion_v3("www." + onion())
    assert not is_onion_v3("oldaddress.onion")
    assert not is_onion_v3("b" + onion()[1:])
    for endpoint in (
        "socks5://127.0.0.1:9050",
        "http://127.0.0.1:9050",
        "socks5h://u:p@127.0.0.1:9050",
        "socks5h://proxy.example:9050",
    ):
        raw = policy(9050).model_dump(by_alias=True)
        raw["tor"]["proxy_endpoint"] = endpoint
        with pytest.raises(ValidationError):
            TransportConfig.model_validate(raw)
    with pytest.raises(ValidationError):
        policy(9050, tor=None)


@pytest.mark.parametrize("dark", [False, True])
def test_curl_uses_proxy_for_robots_and_body_without_local_dns(site, monkeypatch, dark):
    monkeypatch.setenv("NO_PROXY", "*")
    seen = []

    async def scenario():
        server = await socks_server(site[0], seen)
        async with server:
            port = server.sockets[0].getsockname()[1]
            base = state(site)[2].config
            raw = base.model_dump(by_alias=True)
            tp = policy(port).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [site[0]]
            raw["transport"] = tp
            cfg = ChimeraConfig.model_validate(raw)
            host = onion() if dark else "fixture.example"
            scope = Scope(
                allowed_hosts=(host,),
                allowed_ports=(site[0],),
                max_depth=0,
                content_types=("text/html",),
            )
            ladder = FetchLadder((CurlRoute(cfg, resolver=NoLocalDNS()),))
            ledger = Ledger()
            page = await ladder.fetch(
                f"http://{host}:{site[0]}/plain",
                scope,
                RunBudget(cfg, asyncio.get_running_loop().time),
                ledger,
            )
            assert page.body.startswith(b"<article>")
            assert page.transport.mode == "tor"
            assert page.transport.network_class == ("onion" if dark else "open_web")
            assert all(
                row.transport.mode == "tor" for row in ledger.snapshot() if row.event == "fetch"
            )
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    lookups = [row for row in seen if row[0] == 0xF0]
    connections = [row for row in seen if row[0] == 1]
    assert len(connections) == 2
    assert len(lookups) == (0 if dark else 2)
    assert len({row[3] for row in connections}) == 2
    if not dark:
        assert all(row[1] == "127.0.0.1" for row in connections), (
            "pin the Tor-resolved checked address"
        )


def test_direct_onion_refuses_before_local_dns(site):
    cfg = state(site)[2].config
    route = CurlRoute(cfg, resolver=NoLocalDNS())
    from chimera.models import FetchRequest

    with pytest.raises(ChimeraRefused, match="tor_required"):
        asyncio.run(
            route.execute(
                FetchRequest(url=f"http://{onion()}/x", max_bytes=1000, timeout_seconds=1.0)
            )
        )


def test_dead_proxy_and_remote_private_address_never_try_direct(site):
    async def scenario():
        seen = []
        server = await socks_server(site[0], seen, resolved="10.1.2.3")
        async with server:
            raw = state(site)[2].config.model_dump(by_alias=True)
            tp = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [site[0]]
            raw["transport"] = tp
            cfg = ChimeraConfig.model_validate(raw)
            from chimera.models import FetchRequest

            route = CurlRoute(cfg, resolver=NoLocalDNS())
            with pytest.raises(ChimeraRefused, match="out_of_scope"):
                await route.execute(
                    FetchRequest(
                        url=f"http://fixture.example:{site[0]}/plain",
                        max_bytes=1000,
                        timeout_seconds=1.0,
                    )
                )
        assert not any(row[0] == 1 for row in seen)
        # Keep its now-closed exact port; a live proxy must not be substituted.
        with pytest.raises(ChimeraRefused, match="tor_unavailable"):
            await route.execute(
                FetchRequest(
                    url=f"http://fixture.example:{site[0]}/plain",
                    max_bytes=1000,
                    timeout_seconds=1.0,
                )
            )

    before = site[1]["/plain"]
    asyncio.run(scenario())
    assert site[1]["/plain"] == before


def test_cross_network_or_route_redirect_requires_configuration():
    from chimera.transport import validate_transition

    cfg = policy(9050, default_route="direct", rules=[{"host": "example.org", "route": "tor"}])
    with pytest.raises(ChimeraRefused, match="out_of_scope"):
        validate_transition("https://example.org/a", "http://" + onion() + "/x", cfg)
    with pytest.raises(ChimeraRefused, match="out_of_scope"):
        validate_transition("https://example.org/a", "https://other.example/x", cfg)


@pytest.mark.parametrize("dark", [False, True])
def test_proxy_cannot_remove_stream_isolation(site, dark):
    async def scenario():
        seen = []
        server = await socks_server(site[0], seen, auth_method=0)
        async with server:
            raw = state(site)[2].config.model_dump(by_alias=True)
            tp = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [site[0]]
            raw["transport"] = tp
            cfg = ChimeraConfig.model_validate(raw)
            from chimera.models import FetchRequest

            route = CurlRoute(cfg, resolver=NoLocalDNS())
            host = onion() if dark else "fixture.example"
            with pytest.raises(ChimeraRefused, match="tor_unavailable"):
                await route.execute(
                    FetchRequest(
                        url=f"http://{host}:{site[0]}/plain",
                        max_bytes=1000,
                        timeout_seconds=1.0,
                    )
                )
            assert not seen

    before = site[1]["/plain"]
    asyncio.run(scenario())
    assert site[1]["/plain"] == before


def test_tor_request_closes_private_tunnel_and_accounts_partial_bytes(site):
    async def scenario():
        seen = []
        server = await socks_server(site[0], seen)
        async with server:
            raw = state(site)[2].config.model_dump(by_alias=True)
            tp = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [site[0]]
            raw["transport"] = tp
            cfg = ChimeraConfig.model_validate(raw)
            from chimera.models import FetchRequest
            from chimera.refusals import FetchFailure

            with tempfile.TemporaryDirectory(prefix="ct-") as root:
                raw["transport"]["tor"]["bridge_directory"] = root
                cfg = ChimeraConfig.model_validate(raw)
                route = CurlRoute(cfg, resolver=NoLocalDNS())
                with pytest.raises(FetchFailure, match="budget_exhausted") as failure:
                    await route.execute(
                        FetchRequest(
                            url=f"http://fixture.example:{site[0]}/oversized",
                            max_bytes=100,
                            timeout_seconds=1.0,
                        )
                    )
                assert failure.value.bytes_read == 100
                assert not list(Path(root).glob("chimera-tor-*"))

    asyncio.run(scenario())


def test_transport_is_preserved_in_graph_journal_without_changing_legacy_nodes(site, tmp_path):
    from chimera.graph import DirectoryGraphSink, ResearchGraph
    from chimera.models import FetchRequest
    from tests.test_research_graph import policy as graph_policy

    async def scenario():
        server = await socks_server(site[0], [])
        async with server:
            raw = state(site)[2].config.model_dump(by_alias=True)
            tp = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [site[0]]
            raw["transport"] = tp
            route = CurlRoute(ChimeraConfig.model_validate(raw), resolver=NoLocalDNS())
            page = await route.execute(
                FetchRequest(
                    url=f"http://fixture.example:{site[0]}/plain",
                    max_bytes=1000,
                    timeout_seconds=1.0,
                )
            )
            graph_cfg = graph_policy(tmp_path)
            sink = DirectoryGraphSink(graph_cfg, "tor-run")
            graph = ResearchGraph(graph_cfg, "tor-run", sink)
            await graph.start("find evidence")
            await graph.discovered(page.final_url, graph.intent_id)
            await graph.document(
                page.final_url, page.body, "port evidence", "fixture@1", transport=page.transport
            )
            restored = ResearchGraph(graph_cfg, "tor-run", sink)
            await restored.start("find evidence")
            assert restored.snapshot() == graph.snapshot()
            nodes = restored.snapshot().nodes
            document = next(node for node in nodes if node.role == "document")
            assert document.transport == page.transport
            # /1 journals predating transport retain exactly their old field set.
            assert "transport" not in nodes[0].model_dump(mode="json")

    asyncio.run(scenario())


def test_failed_tor_attempt_keeps_selected_route_in_ledger(site):
    async def scenario():
        server = await socks_server(site[0], [], auth_method=0)
        async with server:
            raw = state(site)[2].config.model_dump(by_alias=True)
            tp = policy(server.sockets[0].getsockname()[1]).model_dump(by_alias=True)
            tp["tor"]["allowed_ports"] = [site[0]]
            raw["transport"] = tp
            cfg = ChimeraConfig.model_validate(raw)
            ledger = Ledger()
            ladder = FetchLadder((CurlRoute(cfg, resolver=NoLocalDNS()),))
            with pytest.raises(ChimeraRefused):
                await ladder.fetch(
                    f"http://fixture.example:{site[0]}/plain",
                    state(site)[1],
                    RunBudget(cfg, asyncio.get_running_loop().time),
                    ledger,
                )
            failed = [row for row in ledger.snapshot() if row.event == "fetch" and row.refusal]
            assert failed and failed[0].transport.mode == "tor"
            assert failed[0].refusal.value == "tor_unavailable"
            assert failed[0].transport.proxy_endpoint == tp["tor"]["proxy_endpoint"]

    asyncio.run(scenario())
