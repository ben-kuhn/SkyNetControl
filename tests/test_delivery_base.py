from backend.integrations.delivery.backends.base import DeliveryBackend, DeliveryResult


def test_delivery_result_success():
    result = DeliveryResult(success=True, error=None)
    assert result.success is True
    assert result.error is None


def test_delivery_result_failure():
    result = DeliveryResult(success=False, error="Connection refused")
    assert result.success is False
    assert result.error == "Connection refused"


def test_delivery_backend_is_protocol():
    """DeliveryBackend is a typing Protocol with a send method."""

    class FakeBackend:
        def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
            return DeliveryResult(success=True, error=None)

    backend: DeliveryBackend = FakeBackend()
    result = backend.send("Test", "Body", {})
    assert result.success is True
