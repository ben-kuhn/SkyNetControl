from backend.integrations.delivery.backends.base import DeliveryBackend
from backend.integrations.delivery.backends.email import EmailBackend
from backend.integrations.delivery.backends.groupsio import GroupsIoBackend
from backend.integrations.delivery.backends.winlink import WinlinkBackend

BACKENDS: dict[str, type] = {
    "email": EmailBackend,
    "groupsio": GroupsIoBackend,
    "winlink": WinlinkBackend,
}


def get_backend(name: str) -> DeliveryBackend:
    """Return an instance of the named backend. Raises KeyError if unknown."""
    return BACKENDS[name]()
