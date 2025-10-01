"""Automation tasks for linting, typing, and testing."""
from __future__ import annotations

import nox

nox.options.sessions = ["lint", "typecheck", "tests"]


def _install_project(session: nox.Session) -> None:
    session.install(".")


@nox.session
def lint(session: nox.Session) -> None:
    session.install("ruff")
    session.run("ruff", "format", "--check", "src", "tests")
    session.run("ruff", "check", "src", "tests")


@nox.session
def typecheck(session: nox.Session) -> None:
    _install_project(session)
    session.install("mypy", "types-requests", "types-python-dateutil")
    session.run("mypy", "src")


@nox.session
def tests(session: nox.Session) -> None:
    _install_project(session)
    session.install("pytest")
    session.run("pytest")
