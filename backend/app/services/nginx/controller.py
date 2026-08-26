"""Abstraction over the nginx binary: validate a config, signal a reload.

The engine talks to nginx only through the :class:`NginxController` protocol so
the write/validate/rollback logic can be exercised in tests with a fake
controller — no real nginx, no root, no live traffic. In production
:class:`ShellNginxController` shells out to configurable commands
(``nginx -t`` / ``nginx -s reload`` by default).

Why the commands are configurable: in a split deployment the Celery worker and
the nginx process may not share a binary, so operators can point these at
whatever mechanism reaches their nginx (a wrapper, ``docker exec``, an SSH
shim). The engine's correctness does not depend on which mechanism is used.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of a controller operation."""

    ok: bool
    output: str


@runtime_checkable
class NginxController(Protocol):
    """Something that can validate and reload an nginx configuration."""

    def test(self) -> CommandResult:
        """Run ``nginx -t`` (or equivalent). ``ok`` is True when config is valid."""
        ...

    def reload(self) -> CommandResult:
        """Signal nginx to reload. ``ok`` is True when the reload succeeded."""
        ...


class ShellNginxController:
    """A controller that runs configurable shell commands."""

    def __init__(
        self,
        test_command: str = "nginx -t",
        reload_command: str = "nginx -s reload",
        timeout_seconds: int = 30,
    ) -> None:
        self._test_cmd = shlex.split(test_command)
        self._reload_cmd = shlex.split(reload_command)
        self._timeout = timeout_seconds

    def _run(self, cmd: list[str]) -> CommandResult:
        try:
            proc = subprocess.run(  # noqa: S603 — commands are operator-configured
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return CommandResult(ok=False, output=f"command not found: {exc}")
        except subprocess.TimeoutExpired as exc:
            return CommandResult(ok=False, output=f"timed out after {self._timeout}s: {exc}")
        # nginx writes both -t results and reload errors to stderr.
        output = (proc.stdout or "") + (proc.stderr or "")
        return CommandResult(ok=proc.returncode == 0, output=output.strip())

    def test(self) -> CommandResult:
        return self._run(self._test_cmd)

    def reload(self) -> CommandResult:
        return self._run(self._reload_cmd)


__all__ = ["CommandResult", "NginxController", "ShellNginxController"]
