"""Identity reuse and transport/permission boundaries; no permanent fixture key."""

import os
from pathlib import Path

import pytest
from sentinel_agent import identity


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/?token=secret",
        "https://example.com/#fragment",
    ],
)
def test_identity_rejects_unsafe_servers(value):
    with pytest.raises(ValueError):
        identity.server_url(value)


def test_enrollment_sends_no_account_and_restart_reuses_identity(tmp_path, monkeypatch):
    calls = []
    credential = "sk_host_" + "a" * 50

    def enrolled(server, path, payload):
        calls.append(payload)
        return {"host_id": "test-host-id", "credential": credential}

    monkeypatch.setattr(identity, "post", enrolled)
    destination = tmp_path / "private" / "agent.toml"
    first = identity.enroll("https://api.example.com", "enr_test", destination)
    second = identity.enroll("https://api.example.com", "ignored_on_restart", destination)
    assert first == second and len(calls) == 1
    assert "account_id" not in calls[0] and "host_id" not in calls[0]
    assert identity.load(destination) == first
    if os.name != "nt":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_identity_never_overwrites_existing_file(tmp_path):
    destination = tmp_path / "agent.toml"
    destination.write_text("keep-this-file")
    with pytest.raises(FileExistsError):
        identity.save(
            identity.Identity("https://example.com", "id", "sk_host_" + "x" * 50), destination
        )
    assert destination.read_text() == "keep-this-file"


@pytest.mark.skipif(os.name == "nt", reason="Unix permission boundary")
def test_identity_rejects_shared_permissions_and_symlinks(tmp_path):
    path = tmp_path / "agent.toml"
    identity.save(identity.Identity("https://example.com", "host", "sk_host_" + "x" * 50), path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        identity.load(path)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        identity.load(alias)
    directory = tmp_path / "symlink-directory"
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        identity.save(
            identity.Identity("https://example.com", "host", "sk_host_" + "x" * 50),
            directory / "new.toml",
        )


def test_nonregular_identity_rejected(tmp_path):
    with pytest.raises((ValueError, OSError)):
        identity.load(Path(tmp_path))
