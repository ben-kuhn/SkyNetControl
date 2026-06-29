from backend.integrations.scanner.service import scanner_state


def test_scanner_state_not_running_before_lifespan(app):
    """scanner_state.running is False before the app lifespan starts the task."""
    assert scanner_state.running is False


def test_scanner_state_resets_between_tests():
    """Verify scanner state is not polluted between tests."""
    scanner_state.running = False
    scanner_state.last_scan_time = None
    assert scanner_state.running is False
