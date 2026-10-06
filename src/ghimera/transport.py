"""Injected TCP route preparation; no host proxy or Tor service is modified.

Direct sources use checked pinned DNS. Tor open-web names use Tor's remote
RESOLVE command, then CONNECT to that checked address without re-resolution.
Onion addresses use hostname CONNECT without ordinary DNS. Request-specific
SOCKS isolation is shared by remote lookup and fetch, never logged or exported.
"""

import asyncio
import base64
import hashlib
import ipaddress
import secrets
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from ghimera.config import NetworkPolicy
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.transport_types import TorPolicy, TransportConfig, TransportEvidence


def is_onion_v3(host: str) -> bool:
    parts = host.lower().split(".")
    if len(parts) < 2 or parts[-1] != "onion" or len(parts[-2]) != 56:
        return False
    try:
        value = base64.b32decode(parts[-2].upper())
    except ValueError:
        return False
    return (
        len(value) == 35
        and value[-1] == 3
        and value[32:34]
        == hashlib.sha3_256(b".onion checksum" + value[:32] + value[-1:]).digest()[:2]
    )


def host_port(url: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (
            parsed.scheme not in {"http", "https"}
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or any(ord(char) < 33 for char in url)
        ):
            raise ValueError("unsafe URL")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP literal")
        return host, port
    except ValueError:
        raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE) from None


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class SystemResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            entries = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError:
            raise GhimeraRefused(RefusalCode.FETCH_FAILED) from None
        return tuple(dict.fromkeys(str(item[4][0]) for item in entries))


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    address: str

    @property
    def curl_resolve(self) -> str:
        address = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.host}:{self.port}:{address}"


def checked_address(policy: NetworkPolicy, address: str, port: int) -> None:
    try:
        value = ipaddress.ip_address(address)
        if policy.mode == "public":
            if (
                not value.is_global
                or value.is_multicast
                or value.is_reserved
                or port not in policy.allowed_ports
            ):
                raise ValueError("non-public destination")
        elif str(value) not in policy.fixture_addresses or port not in policy.fixture_ports:
            raise ValueError("undeclared fixture destination")
    except ValueError:
        raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE) from None


class NetworkGuard:
    def __init__(self, policy: NetworkPolicy, resolver: Resolver) -> None:
        self._policy = policy
        self._resolver = resolver

    async def destination(self, url: str) -> Destination:
        host, port = host_port(url)
        if host.endswith(".onion"):
            raise GhimeraRefused(RefusalCode.TOR_REQUIRED)
        addresses = await self._resolver.resolve(host, port)
        if not addresses:
            raise GhimeraRefused(RefusalCode.FETCH_FAILED)
        for address in addresses:
            checked_address(self._policy, address, port)
        return Destination(host, port, addresses[0])


@dataclass(frozen=True)
class PreparedConnection:
    evidence: TransportEvidence
    proxy_endpoint: str = ""
    resolve: tuple[str, ...] = ()
    tunnel: "TorStreamTunnel | None" = field(default=None, repr=False)

    async def close(self) -> None:
        if self.tunnel is not None:
            await self.tunnel.close()


class TorStreamTunnel:
    """A private one-shot Unix socket exposes only an authenticated Tor stream.

    curl keeps the source URL for HTTP Host and TLS SNI but opens this socket,
    so it cannot choose another proxy, resolve the target or connect directly.
    The enclosing temporary directory is 0700; no source bytes are written.
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, directory: Path
    ) -> None:
        self._reader, self._writer = reader, writer
        self._directory = tempfile.TemporaryDirectory(prefix="chimera-tor-", dir=directory)
        self.path = str(Path(self._directory.name) / "stream.sock")
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._clients: set[asyncio.StreamWriter] = set()
        self._claimed = False

    async def start(self) -> None:
        try:
            self._server = await asyncio.start_unix_server(self._accept, path=self.path)
        except BaseException:
            await self.close()
            raise

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._claimed:
            writer.close()
            return
        self._claimed = True
        if self._server is not None:
            self._server.close()
        self._clients.add(writer)
        task = asyncio.create_task(self._relay(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def copy(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
            while chunk := await source.read(65536):
                target.write(chunk)
                await target.drain()

        tasks = (
            asyncio.create_task(copy(reader, self._writer)),
            asyncio.create_task(copy(self._reader, writer)),
        )
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            self._clients.discard(writer)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for writer in self._clients:
            writer.close()
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (OSError, ConnectionError):
            pass
        self._directory.cleanup()


class EgressConnector(Protocol):
    async def prepare(self, url: str, timeout_seconds: float) -> PreparedConnection: ...


class DirectConnector:
    def __init__(self, policy: NetworkPolicy, resolver: Resolver) -> None:
        self._guard = NetworkGuard(policy, resolver)

    async def prepare(self, url: str, timeout_seconds: float) -> PreparedConnection:
        destination = await self._guard.destination(url)
        return PreparedConnection(
            resolve=(destination.curl_resolve,),
            evidence=TransportEvidence(
                schema="chimera.transport-evidence/1",
                mode="direct",
                network_class="open_web",
                connector_revision="direct-pinned/1",
                target_dns="local_pinned",
            ),
        )


class TorConnector:
    def __init__(self, policy: NetworkPolicy, tor: TorPolicy) -> None:
        self._policy = policy
        self._tor = tor

    async def _authenticated_stream(
        self, password: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        endpoint = urlsplit(self._tor.proxy_endpoint)
        if endpoint.hostname is None or endpoint.port is None:
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        reader, writer = await asyncio.open_connection(endpoint.hostname, endpoint.port)
        try:
            writer.write(b"\x05\x01\x02")  # Require username/password stream isolation.
            await writer.drain()
            if await reader.readexactly(2) != b"\x05\x02":
                raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
            username, secret = b"<torS0X>0", password.encode("ascii")
            writer.write(
                b"\x01" + bytes((len(username),)) + username + bytes((len(secret),)) + secret
            )
            await writer.drain()
            if await reader.readexactly(2) != b"\x01\x00":
                raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
            return reader, writer
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise

    async def _socks_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: int,
        host: str,
        port: int,
    ) -> str:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            name = host.encode("idna")
            if len(name) > 255:
                raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE) from None
            target = b"\x03" + bytes((len(name),)) + name
        else:
            target = (b"\x01" if address.version == 4 else b"\x04") + address.packed
        writer.write(b"\x05" + bytes((command,)) + b"\x00" + target + port.to_bytes(2, "big"))
        await writer.drain()
        version, reply, reserved, kind = await reader.readexactly(4)
        if version != 5 or reply != 0 or reserved != 0 or kind not in {1, 3, 4}:
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE)
        if kind == 3:
            size = (await reader.readexactly(1))[0]
            reply_address = (await reader.readexactly(size)).decode("ascii")
        else:
            reply_address = str(
                ipaddress.ip_address(await reader.readexactly(4 if kind == 1 else 16))
            )
        await reader.readexactly(2)
        return reply_address

    async def _remote_resolve(self, host: str, password: str) -> str:
        reader, writer = await self._authenticated_stream(password)
        try:
            return await self._socks_request(reader, writer, 0xF0, host, 0)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _connect(self, host: str, port: int, password: str) -> TorStreamTunnel:
        reader, writer = await self._authenticated_stream(password)
        try:
            await self._socks_request(reader, writer, 1, host, port)
            tunnel = TorStreamTunnel(reader, writer, self._tor.bridge_directory)
            await tunnel.start()
            return tunnel
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise

    async def prepare(self, url: str, timeout_seconds: float) -> PreparedConnection:
        host, port = host_port(url)
        dark = host.endswith(".onion")
        if (
            port not in self._tor.allowed_ports
            or (dark and not self._tor.allow_onion)
            or (not dark and not self._tor.allow_open_web)
        ):
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
        if dark and not is_onion_v3(host):
            raise GhimeraRefused(RefusalCode.INVALID_ONION_ADDRESS)
        isolation = secrets.token_hex(24)
        try:
            async with asyncio.timeout(min(timeout_seconds, self._tor.connect_timeout_seconds)):
                address = host
                if not dark:
                    address = await self._remote_resolve(host, isolation)
                    checked_address(self._policy, address, port)
                tunnel = await self._connect(address, port, isolation)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, UnicodeError):
            raise GhimeraRefused(RefusalCode.TOR_UNAVAILABLE) from None
        return PreparedConnection(
            proxy_endpoint=self._tor.proxy_endpoint,
            tunnel=tunnel,
            evidence=TransportEvidence(
                schema="chimera.transport-evidence/1",
                mode="tor",
                network_class="onion" if dark else "open_web",
                connector_revision="tor-authenticated-stream/1",
                target_dns="onion_no_dns" if dark else "tor_remote_pinned",
                proxy_endpoint=self._tor.proxy_endpoint,
            ),
        )


def validate_transition(previous: str, target: str, config: TransportConfig | None) -> None:
    before, _ = host_port(previous)
    after, _ = host_port(target)
    if before.endswith(".onion") != after.endswith(".onion"):
        if config is None or not config.allow_cross_network_redirects:
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)
    if config is not None and config.mode_for(before) != config.mode_for(after):
        if not config.allow_route_change_redirects:
            raise GhimeraRefused(RefusalCode.OUT_OF_SCOPE)


class RoutingConnector:
    """One owner of transport choice; providers depend on the connection contract."""

    def __init__(
        self, network: NetworkPolicy, config: TransportConfig | None, resolver: Resolver
    ) -> None:
        self._config = config
        self._direct: EgressConnector = DirectConnector(network, resolver)
        self._tor: EgressConnector | None = (
            TorConnector(network, config.tor)
            if config is not None and config.tor is not None
            else None
        )

    async def prepare(self, url: str, timeout_seconds: float) -> PreparedConnection:
        host, _ = host_port(url)
        mode = self._config.mode_for(host) if self._config is not None else "direct"
        if host.endswith(".onion") and self._config is None:
            raise GhimeraRefused(RefusalCode.TOR_REQUIRED)
        if mode == "tor":
            if self._tor is None:
                raise GhimeraRefused(RefusalCode.TOR_REQUIRED)
            return await self._tor.prepare(url, timeout_seconds)
        return await self._direct.prepare(url, timeout_seconds)

    def selection(self, url: str) -> TransportEvidence:
        host, _ = host_port(url)
        dark = host.endswith(".onion")
        mode = self._config.mode_for(host) if self._config is not None else "direct"
        if dark:
            mode = "tor"
        return TransportEvidence(
            schema="chimera.transport-evidence/1",
            mode=mode,
            network_class="onion" if dark else "open_web",
            connector_revision="tor-authenticated-stream/1" if mode == "tor" else "direct-pinned/1",
            target_dns="onion_no_dns"
            if dark
            else "tor_remote_pinned"
            if mode == "tor"
            else "local_pinned",
            proxy_endpoint=(
                self._config.tor.proxy_endpoint
                if mode == "tor" and self._config is not None and self._config.tor is not None
                else None
            ),
        )
