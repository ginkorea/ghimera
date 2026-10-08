"""Remote delivery through the native pinned, bounded private JSON boundary."""

import re
from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator

from ghimera.delivery_sink import DeliverySink
from ghimera.delivery_types import DeliveryAck, DeliveryItem, DeliveryTarget
from ghimera.private_json import JsonWireFailure, PinnedJsonHttp
from ghimera.private_service_config import PrivateJsonConfig
from ghimera.transport import Resolver


class RemoteDeliveryConfig(PrivateJsonConfig):
    schema_version: Literal["ghimera.remote-delivery/1"] = Field(alias="schema")
    target: DeliveryTarget
    credential_environment_variable: (
        Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")] | None
    ) = None

    @model_validator(mode="after")
    def credentials(self) -> "RemoteDeliveryConfig":
        if (self.authorization != "none") != (self.credential_environment_variable is not None):
            raise ValueError(
                "remote delivery authorization requires an explicit credential binding"
            )
        return self


class RemoteDeliverySink(DeliverySink):
    """GET/PUT endpoint/<delivery-id>; PUT and GET return identical durable ACKs.

    DNS answers must belong to the explicitly approved address set. Redirects,
    proxies, oversized replies and calls past their total deadline are refused.
    The receiving service owns durability; an ACK is never model-quality proof.
    """

    def __init__(
        self,
        config: RemoteDeliveryConfig,
        *,
        credential: SecretStr | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.config = RemoteDeliveryConfig.model_validate(config.model_dump())
        self._http = PinnedJsonHttp(self.config, credential=credential, resolver=resolver)

    @property
    def target(self) -> DeliveryTarget:
        return self.config.target

    async def _request(self, identity: str, payload: bytes | None) -> DeliveryAck | None:
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("invalid delivery identity")
        try:
            response = await self._http.request(
                "GET" if payload is None else "PUT", payload or b"", endpoint_suffix="/" + identity
            )
        except JsonWireFailure:
            raise OSError("remote_delivery_unavailable") from None
        if payload is None and response.status == 404:
            return None
        if response.status in {408, 429} or response.status is None or response.status >= 500:
            raise OSError("remote_delivery_unavailable")
        if response.status != 200 or response.content_type != "application/json":
            raise ValueError("remote_delivery_refused")
        ack = DeliveryAck.model_validate_json(response.body)
        if ack.delivery_id != identity or ack.target != self.target:
            raise ValueError("remote acknowledgement differs from pinned identity")
        return ack

    async def lookup(self, delivery_id: str) -> DeliveryAck | None:
        return await self._request(delivery_id, None)

    async def write(self, item: DeliveryItem) -> DeliveryAck:
        ack = await self._request(item.identity, item.model_dump_json().encode())
        if ack is None:
            raise ValueError("remote write did not acknowledge durability")
        return ack
