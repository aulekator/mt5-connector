"""
tests/test_connection.py

Comprehensive tests for MT5Connection.

Every behaviour of connection.py is tested here WITHOUT a real MT5 terminal.
The mock_mt5 fixture patches the mt5 module entirely.

Test groups:
  1.  ConnectionState enum
  2.  Initial state
  3.  Successful connect / disconnect
  4.  initialize() failure
  5.  login() failure
  6.  ensure_connected() all states
  7.  Reconnect (sync) — success path
  8.  Reconnect (sync) — failure / max attempts
  9.  Reconnect (async) — success path
  10. Reconnect (async) — failure / max attempts
  11. get_account_info() — success and failure
  12. get_terminal_info()
  13. last_error()
  14. uptime_seconds()
  15. Context manager (__enter__ / __exit__)
  16. __repr__
  17. Backoff delay calculation
  18. Disconnect when already disconnected (idempotent)
  19. State after login failure stays INITIALIZED (not DISCONNECTED)
  20. reconnect() resets attempt counter on success
"""

import asyncio
import pytest
from unittest.mock import MagicMock, call, patch
from nautilus_mt5.connection import MT5Connection, ConnectionState, AccountSnapshot
from nautilus_mt5.errors import MT5ConnectionError, MT5LoginError


# ═════════════════════════════════════════════════════════════════════════════
# 1. ConnectionState enum
# ═════════════════════════════════════════════════════════════════════════════

class TestConnectionState:

    def test_all_states_exist(self):
        states = {s.name for s in ConnectionState}
        assert states == {
            "DISCONNECTED", "INITIALIZING", "INITIALIZED",
            "LOGGING_IN", "CONNECTED", "RECONNECTING",
            "SHUTTING_DOWN", "FAILED",
        }

    def test_states_are_unique(self):
        values = [s.value for s in ConnectionState]
        assert len(values) == len(set(values))


# ═════════════════════════════════════════════════════════════════════════════
# 2. Initial state
# ═════════════════════════════════════════════════════════════════════════════

class TestInitialState:

    def test_starts_disconnected(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert conn.state == ConnectionState.DISCONNECTED

    def test_not_connected_initially(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert conn.is_connected is False

    def test_uptime_is_none_before_connect(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert conn.uptime_seconds() is None

    def test_repr_shows_disconnected(self, config, mock_mt5):
        conn = MT5Connection(config)
        r = repr(conn)
        assert "DISCONNECTED" in r
        assert "12345678" in r
        assert "Exness-MT5Trial1" in r


# ═════════════════════════════════════════════════════════════════════════════
# 3. Successful connect / disconnect
# ═════════════════════════════════════════════════════════════════════════════

class TestSuccessfulConnect:

    def test_connect_calls_initialize_and_login(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        mock_mt5.initialize.assert_called_once()
        mock_mt5.login.assert_called_once_with(
            login=12345678,
            password="test_password",
            server="Exness-MT5Trial1",
            timeout=5000,
        )

    def test_state_is_connected_after_connect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        assert conn.state == ConnectionState.CONNECTED

    def test_is_connected_true_after_connect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        assert conn.is_connected is True

    def test_uptime_positive_after_connect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        uptime = conn.uptime_seconds()
        assert uptime is not None
        assert uptime >= 0.0

    def test_disconnect_calls_mt5_shutdown(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        conn.disconnect()
        mock_mt5.shutdown.assert_called_once()

    def test_state_is_disconnected_after_disconnect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        conn.disconnect()
        assert conn.state == ConnectionState.DISCONNECTED

    def test_uptime_is_none_after_disconnect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        conn.disconnect()
        assert conn.uptime_seconds() is None

    def test_repr_shows_connected(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        assert "CONNECTED" in repr(conn)


# ═════════════════════════════════════════════════════════════════════════════
# 4. initialize() failure
# ═════════════════════════════════════════════════════════════════════════════

class TestInitializeFailure:

    def test_raises_connection_error_when_initialize_fails(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError) as exc_info:
            conn.connect()
        assert "mt5.initialize() failed" in str(exc_info.value)
        assert "5" in str(exc_info.value)
        assert "IPC timeout" in str(exc_info.value)

    def test_state_is_disconnected_after_initialize_failure(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError):
            conn.connect()
        assert conn.state == ConnectionState.DISCONNECTED

    def test_helpful_message_mentions_terminal(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError) as exc_info:
            conn.connect()
        assert "MT5 terminal" in str(exc_info.value)


# ═════════════════════════════════════════════════════════════════════════════
# 5. login() failure
# ═════════════════════════════════════════════════════════════════════════════

class TestLoginFailure:

    def test_raises_login_error_when_login_fails(self, config, mock_mt5):
        mock_mt5.login.return_value = False
        mock_mt5.last_error.return_value = (65537, "Invalid account")
        conn = MT5Connection(config)
        with pytest.raises(MT5LoginError) as exc_info:
            conn.connect()
        assert "mt5.login() failed" in str(exc_info.value)
        assert "12345678" in str(exc_info.value)

    def test_state_stays_initialized_after_login_failure(self, config, mock_mt5):
        """Terminal IPC is up, only login failed — state must be INITIALIZED not DISCONNECTED."""
        mock_mt5.login.return_value = False
        mock_mt5.last_error.return_value = (65537, "Invalid account")
        conn = MT5Connection(config)
        with pytest.raises(MT5LoginError):
            conn.connect()
        assert conn.state == ConnectionState.INITIALIZED

    def test_login_error_mentions_server(self, config, mock_mt5):
        mock_mt5.login.return_value = False
        mock_mt5.last_error.return_value = (65537, "Invalid account")
        conn = MT5Connection(config)
        with pytest.raises(MT5LoginError) as exc_info:
            conn.connect()
        assert "Exness-MT5Trial1" in str(exc_info.value)

    def test_login_not_called_if_initialize_failed(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError):
            conn.connect()
        mock_mt5.login.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 6. ensure_connected() — all states
# ═════════════════════════════════════════════════════════════════════════════

class TestEnsureConnected:

    def test_passes_when_connected(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        conn.ensure_connected()  # must not raise

    def test_raises_when_disconnected(self, config, mock_mt5):
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError) as exc_info:
            conn.ensure_connected()
        assert "DISCONNECTED" in str(exc_info.value)

    def test_raises_when_failed(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._state = ConnectionState.FAILED
        with pytest.raises(MT5ConnectionError) as exc_info:
            conn.ensure_connected()
        assert "permanently failed" in str(exc_info.value)
        assert str(config.reconnect_max_attempts) in str(exc_info.value)

    def test_raises_when_initializing(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._state = ConnectionState.INITIALIZING
        with pytest.raises(MT5ConnectionError):
            conn.ensure_connected()

    def test_raises_when_reconnecting(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._state = ConnectionState.RECONNECTING
        with pytest.raises(MT5ConnectionError):
            conn.ensure_connected()

    def test_is_fast_when_connected(self, config, mock_mt5):
        """ensure_connected() must be O(1) — no mt5 calls in the fast path."""
        conn = MT5Connection(config)
        conn.connect()
        initial_call_count = mock_mt5.terminal_info.call_count
        for _ in range(1000):
            conn.ensure_connected()
        # No additional mt5 calls from ensure_connected itself
        assert mock_mt5.terminal_info.call_count == initial_call_count


# ═════════════════════════════════════════════════════════════════════════════
# 7. Reconnect (sync) — success path
# ═════════════════════════════════════════════════════════════════════════════

class TestReconnectSync:

    def test_reconnect_succeeds_first_attempt(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._state = ConnectionState.RECONNECTING
        result = conn.reconnect()
        assert result is True

    def test_reconnect_calls_shutdown_then_initialize_then_login(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._state = ConnectionState.RECONNECTING
        conn.reconnect()
        mock_mt5.shutdown.assert_called()
        mock_mt5.initialize.assert_called()
        mock_mt5.login.assert_called()

    def test_reconnect_state_is_connected_on_success(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._state = ConnectionState.RECONNECTING
        conn.reconnect()
        assert conn.state == ConnectionState.CONNECTED

    def test_reconnect_resets_attempt_counter_on_success(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._attempt = 2
        conn.reconnect()
        assert conn._attempt == 0

    def test_reconnect_succeeds_after_initial_failures(self, config, mock_mt5):
        """Fails first 2 attempts, succeeds on 3rd."""
        call_count = {"n": 0}
        def flaky_init():
            call_count["n"] += 1
            return call_count["n"] >= 3  # fail twice, then succeed
        mock_mt5.initialize.side_effect = flaky_init

        conn = MT5Connection(config)
        result = conn.reconnect()
        assert result is True
        assert mock_mt5.initialize.call_count == 3


# ═════════════════════════════════════════════════════════════════════════════
# 8. Reconnect (sync) — failure / max attempts
# ═════════════════════════════════════════════════════════════════════════════

class TestReconnectSyncFailure:

    def test_reconnect_returns_false_when_max_attempts_exceeded(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        result = conn.reconnect()
        assert result is False

    def test_state_is_failed_when_max_attempts_exceeded(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        conn.reconnect()
        assert conn.state == ConnectionState.FAILED

    def test_attempt_count_equals_max_on_failure(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        conn.reconnect()
        # config.reconnect_max_attempts == 3 in test fixture
        assert conn._attempt == config.reconnect_max_attempts

    def test_ensure_connected_raises_after_failed_reconnect(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        conn.reconnect()
        with pytest.raises(MT5ConnectionError) as exc_info:
            conn.ensure_connected()
        assert "permanently failed" in str(exc_info.value)


# ═════════════════════════════════════════════════════════════════════════════
# 9. Reconnect (async) — success path
# ═════════════════════════════════════════════════════════════════════════════

class TestReconnectAsync:

    @pytest.mark.asyncio
    async def test_async_reconnect_succeeds(self, config, mock_mt5):
        conn = MT5Connection(config)
        result = await conn.reconnect_async()
        assert result is True

    @pytest.mark.asyncio
    async def test_async_reconnect_state_is_connected(self, config, mock_mt5):
        conn = MT5Connection(config)
        await conn.reconnect_async()
        assert conn.state == ConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_async_reconnect_resets_attempt_counter(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn._attempt = 1
        await conn.reconnect_async()
        assert conn._attempt == 0

    @pytest.mark.asyncio
    async def test_async_reconnect_succeeds_after_initial_failures(self, config, mock_mt5):
        call_count = {"n": 0}
        def flaky_init():
            call_count["n"] += 1
            return call_count["n"] >= 2
        mock_mt5.initialize.side_effect = flaky_init

        conn = MT5Connection(config)
        result = await conn.reconnect_async()
        assert result is True


# ═════════════════════════════════════════════════════════════════════════════
# 10. Reconnect (async) — failure / max attempts
# ═════════════════════════════════════════════════════════════════════════════

class TestReconnectAsyncFailure:

    @pytest.mark.asyncio
    async def test_async_reconnect_returns_false_on_exhaustion(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        result = await conn.reconnect_async()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_reconnect_state_is_failed(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        await conn.reconnect_async()
        assert conn.state == ConnectionState.FAILED


# ═════════════════════════════════════════════════════════════════════════════
# 11. get_account_info()
# ═════════════════════════════════════════════════════════════════════════════

class TestGetAccountInfo:

    def test_returns_account_snapshot(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        info = conn.get_account_info()
        assert isinstance(info, AccountSnapshot)

    def test_account_fields_correct(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        info = conn.get_account_info()
        assert info.login == 12345678
        assert info.server == "Exness-MT5Trial1"
        assert info.balance == 10000.00
        assert info.currency == "USD"
        assert info.leverage == 2000

    def test_raises_if_not_connected(self, config, mock_mt5):
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError):
            conn.get_account_info()

    def test_raises_when_account_info_returns_none(self, config, mock_mt5):
        mock_mt5.account_info.return_value = None
        mock_mt5.last_error.return_value = (6, "No connection")
        conn = MT5Connection(config)
        conn.connect()
        with pytest.raises(MT5ConnectionError) as exc_info:
            conn.get_account_info()
        assert "None" in str(exc_info.value)

    def test_account_snapshot_str_contains_balance(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        info = conn.get_account_info()
        s = str(info)
        assert "10000.00" in s
        assert "USD" in s
        assert "2000" in s


# ═════════════════════════════════════════════════════════════════════════════
# 12. get_terminal_info()
# ═════════════════════════════════════════════════════════════════════════════

class TestGetTerminalInfo:

    def test_returns_dict(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        info = conn.get_terminal_info()
        assert isinstance(info, dict)

    def test_contains_expected_keys(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        info = conn.get_terminal_info()
        assert "name" in info
        assert "connected" in info
        assert "ping_last" in info

    def test_returns_empty_dict_when_terminal_info_none(self, config, mock_mt5):
        mock_mt5.terminal_info.return_value = None
        conn = MT5Connection(config)
        conn.connect()
        assert conn.get_terminal_info() == {}

    def test_raises_when_not_connected(self, config, mock_mt5):
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError):
            conn.get_terminal_info()


# ═════════════════════════════════════════════════════════════════════════════
# 13. last_error()
# ═════════════════════════════════════════════════════════════════════════════

class TestLastError:

    def test_returns_tuple(self, config, mock_mt5):
        mock_mt5.last_error.return_value = (0, "No error")
        conn = MT5Connection(config)
        result = conn.last_error()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_safe_before_connect(self, config, mock_mt5):
        """last_error() must work without any connection."""
        mock_mt5.last_error.return_value = (0, "No error")
        conn = MT5Connection(config)
        code, msg = conn.last_error()
        assert code == 0


# ═════════════════════════════════════════════════════════════════════════════
# 14. uptime_seconds()
# ═════════════════════════════════════════════════════════════════════════════

class TestUptime:

    def test_none_before_connect(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert conn.uptime_seconds() is None

    def test_positive_after_connect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        assert conn.uptime_seconds() >= 0.0

    def test_none_after_disconnect(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        conn.disconnect()
        assert conn.uptime_seconds() is None


# ═════════════════════════════════════════════════════════════════════════════
# 15. Context manager
# ═════════════════════════════════════════════════════════════════════════════

class TestContextManager:

    def test_connects_on_enter(self, config, mock_mt5):
        with MT5Connection(config) as conn:
            assert conn.is_connected is True

    def test_disconnects_on_exit(self, config, mock_mt5):
        with MT5Connection(config) as conn:
            pass
        assert conn.state == ConnectionState.DISCONNECTED

    def test_disconnects_on_exception(self, config, mock_mt5):
        conn = None
        try:
            with MT5Connection(config) as c:
                conn = c
                raise ValueError("strategy error")
        except ValueError:
            pass
        assert conn.state == ConnectionState.DISCONNECTED

    def test_does_not_suppress_exceptions(self, config, mock_mt5):
        with pytest.raises(ValueError):
            with MT5Connection(config):
                raise ValueError("should propagate")


# ═════════════════════════════════════════════════════════════════════════════
# 16. __repr__
# ═════════════════════════════════════════════════════════════════════════════

class TestRepr:

    def test_repr_contains_account(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert "12345678" in repr(conn)

    def test_repr_contains_server(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert "Exness-MT5Trial1" in repr(conn)

    def test_repr_contains_state(self, config, mock_mt5):
        conn = MT5Connection(config)
        assert "DISCONNECTED" in repr(conn)
        conn.connect()
        assert "CONNECTED" in repr(conn)


# ═════════════════════════════════════════════════════════════════════════════
# 17. Backoff delay calculation
# ═════════════════════════════════════════════════════════════════════════════

class TestBackoffDelays:

    def test_delay_doubles_each_attempt(self, config, mock_mt5):
        """Verify the exponential backoff math without actually sleeping."""
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")

        delays_seen = []
        original_sleep = __import__("time").sleep

        import time as time_module
        with patch("time.sleep", side_effect=lambda d: delays_seen.append(d)):
            conn = MT5Connection(config)
            conn.reconnect()

        assert len(delays_seen) == config.reconnect_max_attempts
        # Each delay should be <= the next (exponential growth)
        for i in range(len(delays_seen) - 1):
            assert delays_seen[i] <= delays_seen[i + 1]

    def test_delay_is_capped_at_max(self, config, mock_mt5):
        """Delays must never exceed reconnect_max_delay_s."""
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")

        delays_seen = []
        with patch("time.sleep", side_effect=lambda d: delays_seen.append(d)):
            conn = MT5Connection(config)
            conn.reconnect()

        for d in delays_seen:
            assert d <= config.reconnect_max_delay_s


# ═════════════════════════════════════════════════════════════════════════════
# 18. Disconnect idempotency
# ═════════════════════════════════════════════════════════════════════════════

class TestDisconnectIdempotency:

    def test_disconnect_when_already_disconnected_does_not_raise(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.disconnect()  # already DISCONNECTED — must not raise
        conn.disconnect()  # again — still must not raise

    def test_disconnect_only_calls_shutdown_once(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        conn.disconnect()
        conn.disconnect()  # second call should be a no-op
        mock_mt5.shutdown.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# 19. State integrity after login failure
# ═════════════════════════════════════════════════════════════════════════════

class TestStateIntegrity:

    def test_login_failure_leaves_state_initialized_not_disconnected(self, config, mock_mt5):
        """
        When login fails, terminal IPC is still alive (INITIALIZED state).
        State must NOT be DISCONNECTED — that would hide the terminal being open.
        """
        mock_mt5.login.return_value = False
        mock_mt5.last_error.return_value = (65537, "Invalid account")
        conn = MT5Connection(config)
        with pytest.raises(MT5LoginError):
            conn.connect()
        assert conn.state == ConnectionState.INITIALIZED
        assert conn.state != ConnectionState.DISCONNECTED

    def test_initialize_failure_leaves_state_disconnected(self, config, mock_mt5):
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
        conn = MT5Connection(config)
        with pytest.raises(MT5ConnectionError):
            conn.connect()
        assert conn.state == ConnectionState.DISCONNECTED


# ═════════════════════════════════════════════════════════════════════════════
# 20. AccountSnapshot
# ═════════════════════════════════════════════════════════════════════════════

class TestAccountSnapshot:

    def test_from_mt5_builds_correctly(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        snap = conn.get_account_info()
        assert snap.login == 12345678
        assert snap.equity == 10050.25
        assert snap.leverage == 2000
        assert snap.company == "Exness Technologies Ltd"

    def test_str_contains_all_key_fields(self, config, mock_mt5):
        conn = MT5Connection(config)
        conn.connect()
        snap = conn.get_account_info()
        s = str(snap)
        assert "12345678" in s
        assert "10000.00" in s
        assert "USD" in s
        assert "2000" in s