import pytest

from ai_edda.access import OwnerAuth, WriterLeases


def test_owner_sessions(tmp_path):
    path = tmp_path / "owner.json"
    OwnerAuth.setup(path, "secret")
    assert "secret" not in path.read_text()
    with pytest.raises(FileExistsError):
        OwnerAuth.setup(path, "other")
    now = [100]
    auth = OwnerAuth(path, clock=lambda: now[0], session_ttl=5)
    assert auth.login("wrong") is None
    token = auth.login("secret")
    assert auth.authenticate(token)
    assert not OwnerAuth(path, clock=lambda: now[0]).authenticate(token)
    now[0] += 5
    assert not auth.authenticate(token)
    token = auth.login("secret")
    assert auth.logout(token)
    assert not auth.authenticate(token)


def test_writer_leases():
    now = [0]
    leases = WriterLeases(clock=lambda: now[0])
    first = leases.acquire("p", "a")
    assert leases.acquire("p", "a") == first
    assert leases.acquire("p", "b") is None
    second = leases.acquire("p", "b", takeover=True)
    assert not leases.require("p", "a", first)
    assert not leases.heartbeat("p", "a", first)
    now[0] = 59
    assert leases.heartbeat("p", "b", second)
    now[0] = 60
    assert leases.require("p", "b", second)
    now[0] = 119
    assert not leases.require("p", "b", second)
    assert leases.acquire("p", "a")


def test_expired_lease_does_not_revive_after_clock_rollback():
    now = [0]
    leases = WriterLeases(clock=lambda: now[0], ttl=5)
    token = leases.acquire("p", "a")
    now[0] = 5
    assert not leases.heartbeat("p", "a", token)
    now[0] = 1
    assert not leases.heartbeat("p", "a", token)
    assert not leases.require("p", "a", token)


def test_default_clock_uses_monotonic_before_constructor_binding(tmp_path, monkeypatch):
    import importlib.util

    import ai_edda.access as access_module

    path = tmp_path / "owner.json"
    OwnerAuth.setup(path, "secret")
    clock = [100]
    monkeypatch.setattr("ai_edda.access.time.monotonic", lambda: clock[0])
    # The constructor default captured the original clock at module import.
    # Load an isolated module after patching to control its bound default.
    spec = importlib.util.spec_from_file_location(
        "access_with_fake_monotonic", access_module.__file__
    )
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    auth = isolated.OwnerAuth(path, session_ttl=5)
    token = auth.login("secret")
    clock[0] = 105
    assert not auth.authenticate(token)
