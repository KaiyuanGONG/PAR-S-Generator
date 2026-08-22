from __future__ import annotations

import socket

import pytest

from windows_launcher import AlreadyRunningError, SingleInstanceLock, select_loopback_port


def test_port_selection_stays_on_loopback_and_skips_a_conflict() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    conflict = occupied.getsockname()[1]
    try:
        selected = select_loopback_port(conflict, attempts=4)
    finally:
        occupied.close()

    assert selected != conflict
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", selected))
    finally:
        probe.close()


def test_single_instance_lock_rejects_a_second_process_and_releases(tmp_path) -> None:
    lock_path = tmp_path / "windows-v1.lock"

    with SingleInstanceLock(lock_path):
        with pytest.raises(AlreadyRunningError):
            with SingleInstanceLock(lock_path):
                pass

    with SingleInstanceLock(lock_path):
        assert lock_path.is_file()
