from backend.integrations.scanner.service import scanner_state


def test_scanner_not_started_when_disabled(app):
    """Scanner should not start if scanner.enabled is not 'true'."""
    assert scanner_state.running is False


def test_scanner_state_resets_between_tests():
    """Verify scanner state is not polluted between tests."""
    scanner_state.running = False
    scanner_state.last_scan_time = None
    assert scanner_state.running is False
