"""nginx config generation and reload engine.

Public facade over the pieces:

* :func:`render_config` — pure DesiredState → ``{filename: contents}``.
* :func:`load_desired_state` / :func:`load_desired_state_sync` — DB → DesiredState.
* :func:`apply_config` — write + validate + reload with rollback.
* :func:`build_controller` — an :class:`NginxController` from app settings.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.nginx.controller import (
    CommandResult,
    NginxController,
    ShellNginxController,
)
from app.services.nginx.engine import ApplyResult, apply_config
from app.services.nginx.loader import load_desired_state, load_desired_state_sync
from app.services.nginx.renderer import render_config, render_stream_config
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
    StreamSpec,
    UpstreamSpec,
)


def build_controller() -> NginxController:
    """Construct the configured shell-backed nginx controller from settings."""
    return ShellNginxController(
        test_command=settings.nginx_test_command,
        reload_command=settings.nginx_reload_command,
    )


__all__ = [
    "ApplyResult",
    "BackendSpec",
    "CertificateSpec",
    "CommandResult",
    "DeadHostSpec",
    "DesiredState",
    "NginxController",
    "ProxyHostSpec",
    "RedirectionHostSpec",
    "ShellNginxController",
    "StreamSpec",
    "UpstreamSpec",
    "apply_config",
    "build_controller",
    "load_desired_state",
    "load_desired_state_sync",
    "render_config",
    "render_stream_config",
]
