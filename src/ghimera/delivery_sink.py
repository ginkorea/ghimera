"""Final read-before-write/readback invariant around interchangeable destinations."""

from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import final

from ghimera.delivery_types import DeliveryAck, DeliveryItem, DeliveryTarget


class DeliverySink(ABC):
    """At-least-once callers require an idempotent, immutable destination.

    Implementations own entitlement, transport and durability. Lookup must not
    acknowledge pending or volatile copies. No implicit network destinations.
    """

    REFERENCE = MappingProxyType({"durable_readback": "DirectoryDeliverySink"})

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if "deliver" in cls.__dict__:
            raise TypeError("DeliverySink.deliver is final; implement lookup and write")

    @property
    @abstractmethod
    def target(self) -> DeliveryTarget: ...

    @abstractmethod
    async def lookup(self, delivery_id: str) -> DeliveryAck | None: ...

    @abstractmethod
    async def write(self, item: DeliveryItem) -> DeliveryAck: ...

    @final
    async def deliver(self, item: DeliveryItem) -> DeliveryAck:
        item = DeliveryItem.model_validate(item.model_dump())
        if DeliveryTarget.model_validate(self.target.model_dump()) != item.target:
            raise ValueError("delivery destination must match the item's pinned target")
        previous = await self.lookup(item.identity)
        if previous is not None:
            previous = DeliveryAck.model_validate(previous.model_dump())
            previous.validate_item(item)
            return previous
        written = DeliveryAck.model_validate((await self.write(item)).model_dump())
        written.validate_item(item)
        observed = await self.lookup(item.identity)
        if observed is None:
            raise ValueError("delivery write has no durable destination readback")
        observed = DeliveryAck.model_validate(observed.model_dump())
        observed.validate_item(item)
        if observed != written:
            raise ValueError("delivery write and durable readback acknowledgements differ")
        return observed
