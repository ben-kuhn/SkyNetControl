from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class DeliveryResult:
    success: bool
    error: str | None


@runtime_checkable
class DeliveryBackend(Protocol):
    def send(self, subject: str, body: str, config: dict) -> DeliveryResult: ...
