"""Tests for the cross-process single-instance guard."""
from __future__ import annotations

import importlib
import sys

import pytest


def _fresh_module():
    sys.modules.pop("single_instance", None)
    return importlib.import_module("single_instance")


@pytest.mark.skipif(
    not hasattr(__import__("ctypes"), "windll"),
    reason="named-mutex guard is Windows-only",
)
def test_second_acquire_same_name_returns_false():
    mod = _fresh_module()
    name = "TokenTray-Test-SingleInstance-Mutex"
    assert mod.acquire_single_instance(name) is True
    # A second attempt at the same name (even within one process) must report
    # that an instance already holds the lock.
    assert mod.acquire_single_instance(name) is False


@pytest.mark.skipif(
    not hasattr(__import__("ctypes"), "windll"),
    reason="named-mutex guard is Windows-only",
)
def test_distinct_names_both_acquire():
    mod = _fresh_module()
    assert mod.acquire_single_instance("TokenTray-Test-Name-A") is True
    assert mod.acquire_single_instance("TokenTray-Test-Name-B") is True
