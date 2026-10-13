"""The cross-process file lock behind the partition write lock and the single-writer lease."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ingest.filelock import FileLock, LockTimeoutError


def test_a_second_lock_on_the_same_path_waits_for_the_first(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    first, second = FileLock(path), FileLock(path)
    assert first.try_acquire()
    assert first.held
    assert not second.try_acquire()
    first.release()
    assert not first.held
    assert second.try_acquire()
    second.release()


def test_acquire_times_out_instead_of_waiting_forever(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    holder = FileLock(path)
    assert holder.try_acquire()
    try:
        with pytest.raises(LockTimeoutError):
            FileLock(path).acquire(timeout=0.2)
    finally:
        holder.release()


def test_the_context_manager_releases_even_when_the_body_fails(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    with pytest.raises(RuntimeError), FileLock(path):
        raise RuntimeError("boom")
    probe = FileLock(path)
    assert probe.try_acquire()
    probe.release()


def test_locking_twice_with_one_object_is_a_bug_not_a_deadlock(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "x.lock")
    assert lock.try_acquire()
    try:
        with pytest.raises(RuntimeError):
            lock.try_acquire()
    finally:
        lock.release()


def test_a_lock_held_by_another_process_blocks_and_is_freed_when_it_exits_abruptly(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.lock"
    script = textwrap.dedent(
        """
        import os, sys
        from ingest.filelock import FileLock
        lock = FileLock(sys.argv[1])
        assert lock.try_acquire()
        print("locked", flush=True)
        sys.stdin.readline()
        os._exit(1)  # no cleanup, no release: like a crash
        """
    )
    child = subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-c", script, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdin is not None
        assert child.stdout.readline().strip() == "locked"
        assert not FileLock(path).try_acquire()  # held by the other process
        child.stdin.write("die" + chr(10))
        child.stdin.flush()
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
    # The operating system released it when the process died: no stale lock to clean up.
    after = FileLock(path)
    assert after.try_acquire()
    after.release()
