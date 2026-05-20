import pytest

from backend.integrations.delivery.backends import BACKENDS, get_backend
from backend.integrations.delivery.backends.base import DeliveryBackend
from backend.integrations.delivery.backends.email import EmailBackend
from backend.integrations.delivery.backends.groupsio import GroupsIoBackend
from backend.integrations.delivery.backends.winlink import WinlinkBackend


def test_backends_registry_has_all_backends():
    assert "email" in BACKENDS
    assert "groupsio" in BACKENDS
    assert "winlink" in BACKENDS
    assert len(BACKENDS) == 3


def test_get_backend_returns_correct_type():
    assert isinstance(get_backend("email"), EmailBackend)
    assert isinstance(get_backend("groupsio"), GroupsIoBackend)
    assert isinstance(get_backend("winlink"), WinlinkBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(KeyError):
        get_backend("pigeon")
