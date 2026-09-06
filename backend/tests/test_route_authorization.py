"""Every route the app exposes, and the role it demands.

The guards themselves are one-line dependencies scattered over twenty route
modules, so "did we remember?" is not a question care can answer. This asks it
of the running app instead: it resolves each route's real dependency tree and
compares it against the table below. A new endpoint that is not in the table
fails here, which is the point — declaring its role is part of adding it.
"""

from __future__ import annotations

import pytest
from app.main import app


def iter_routes(router, prefix: str = ""):
    """Every endpoint under ``router``, including those behind included routers.

    FastAPI 0.141 wraps an included router in ``_IncludedRouter`` instead of
    copying its routes up, so recursion has to go through ``original_router``.
    """
    for route in getattr(router, "routes", []):
        if type(route).__name__ == "_IncludedRouter":
            context = getattr(route, "include_context", None)
            inner_prefix = prefix + (context.prefix if context else "")
            yield from iter_routes(route.original_router, inner_prefix)
        elif getattr(route, "dependant", None) is not None:
            for method in sorted(set(route.methods or ()) - {"HEAD", "OPTIONS"}):
                yield method, prefix + route.path, route
        elif getattr(route, "routes", None):
            yield from iter_routes(route, prefix)


def route_guard(route) -> str:
    """The strongest guard the route actually enforces."""
    seen: list[str] = []

    def visit(dependant) -> None:
        for sub in dependant.dependencies:
            if sub.call is not None:
                seen.append(getattr(sub.call, "__name__", ""))
            visit(sub)

    visit(route.dependant)
    if "require_admin" in seen:
        return "admin"
    if "get_current_user" in seen:
        return "member"
    return "public"


#: The role each route demands. Seeded from the app as it stood on 2026-09-05
#: and edited deliberately from there — see the task that changes each one.
ROUTE_ROLES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/access-lists"): "member",
    ("POST", "/api/v1/access-lists"): "admin",
    ("DELETE", "/api/v1/access-lists/{access_list_id}"): "admin",
    ("GET", "/api/v1/access-lists/{access_list_id}"): "member",
    ("PATCH", "/api/v1/access-lists/{access_list_id}"): "admin",
    ("POST", "/api/v1/access-lists/{access_list_id}/auth-users"): "admin",
    ("DELETE", "/api/v1/access-lists/{access_list_id}/auth-users/{user_id}"): "admin",
    ("PATCH", "/api/v1/access-lists/{access_list_id}/auth-users/{user_id}"): "admin",
    ("POST", "/api/v1/access-lists/{access_list_id}/clients"): "admin",
    ("DELETE", "/api/v1/access-lists/{access_list_id}/clients/{rule_id}"): "admin",
    ("PATCH", "/api/v1/access-lists/{access_list_id}/clients/{rule_id}"): "admin",
    ("GET", "/api/v1/audit-log"): "admin",
    ("POST", "/api/v1/auth/accept-invite"): "public",
    ("GET", "/api/v1/auth/capabilities"): "public",
    ("POST", "/api/v1/auth/forgot-password"): "public",
    ("POST", "/api/v1/auth/login"): "public",
    ("GET", "/api/v1/auth/me"): "member",
    ("POST", "/api/v1/auth/mfa/passkey/options"): "public",
    ("POST", "/api/v1/auth/mfa/passkey/verify"): "public",
    ("POST", "/api/v1/auth/mfa/verify"): "public",
    ("POST", "/api/v1/auth/refresh"): "public",
    ("POST", "/api/v1/auth/reset-password"): "public",
    ("GET", "/api/v1/certificates"): "member",
    ("POST", "/api/v1/certificates/custom"): "admin",
    ("POST", "/api/v1/certificates/letsencrypt"): "admin",
    ("DELETE", "/api/v1/certificates/{cert_id}"): "admin",
    ("GET", "/api/v1/certificates/{cert_id}"): "member",
    ("POST", "/api/v1/certificates/{cert_id}/renew"): "admin",
    ("GET", "/api/v1/cluster/status"): "admin",
    ("GET", "/api/v1/crowdsec/alerts"): "member",
    ("GET", "/api/v1/crowdsec/alerts/{alert_id}"): "member",
    ("GET", "/api/v1/crowdsec/decisions"): "member",
    ("POST", "/api/v1/crowdsec/capi/register"): "admin",
    ("POST", "/api/v1/crowdsec/decisions"): "admin",
    ("DELETE", "/api/v1/crowdsec/decisions/{decision_id}"): "admin",
    ("GET", "/api/v1/crowdsec/health"): "member",
    ("POST", "/api/v1/crowdsec/hub/update"): "admin",
    ("GET", "/api/v1/crowdsec/maintenance"): "admin",
    ("GET", "/api/v1/crowdsec/whitelists"): "member",
    ("POST", "/api/v1/crowdsec/whitelists"): "admin",
    ("POST", "/api/v1/crowdsec/whitelists/apply"): "admin",
    ("POST", "/api/v1/crowdsec/whitelists/preview"): "admin",
    ("GET", "/api/v1/crowdsec/whitelists/status"): "member",
    ("DELETE", "/api/v1/crowdsec/whitelists/{whitelist_id}"): "admin",
    ("PATCH", "/api/v1/crowdsec/whitelists/{whitelist_id}"): "admin",
    ("GET", "/api/v1/custom-pages"): "member",
    ("POST", "/api/v1/custom-pages"): "admin",
    ("POST", "/api/v1/custom-pages/assist"): "admin",
    ("DELETE", "/api/v1/custom-pages/{page_id}"): "admin",
    ("GET", "/api/v1/custom-pages/{page_id}"): "member",
    ("PATCH", "/api/v1/custom-pages/{page_id}"): "admin",
    ("GET", "/api/v1/dashboard/summary"): "member",
    ("GET", "/api/v1/dashboard/threats"): "member",
    ("GET", "/api/v1/dashboard/visitors"): "member",
    ("GET", "/api/v1/dead-hosts"): "member",
    ("POST", "/api/v1/dead-hosts"): "admin",
    ("DELETE", "/api/v1/dead-hosts/{host_id}"): "admin",
    ("GET", "/api/v1/dead-hosts/{host_id}"): "member",
    ("PATCH", "/api/v1/dead-hosts/{host_id}"): "admin",
    ("GET", "/api/v1/dns-credentials"): "admin",
    ("POST", "/api/v1/dns-credentials"): "admin",
    ("DELETE", "/api/v1/dns-credentials/{credential_id}"): "admin",
    ("PATCH", "/api/v1/dns-credentials/{credential_id}"): "admin",
    ("POST", "/api/v1/dns-credentials/{credential_id}/verify"): "admin",
    ("GET", "/api/v1/dns-providers"): "admin",
    ("GET", "/api/v1/events"): "admin",
    ("GET", "/api/v1/nginx/preview"): "admin",
    ("POST", "/api/v1/nginx/reload"): "admin",
    ("GET", "/api/v1/proxy-hosts"): "member",
    ("POST", "/api/v1/proxy-hosts"): "admin",
    ("DELETE", "/api/v1/proxy-hosts/{host_id}"): "admin",
    ("GET", "/api/v1/proxy-hosts/{host_id}"): "member",
    ("PATCH", "/api/v1/proxy-hosts/{host_id}"): "admin",
    ("GET", "/api/v1/redirection-hosts"): "member",
    ("POST", "/api/v1/redirection-hosts"): "admin",
    ("DELETE", "/api/v1/redirection-hosts/{host_id}"): "admin",
    ("GET", "/api/v1/redirection-hosts/{host_id}"): "member",
    ("PATCH", "/api/v1/redirection-hosts/{host_id}"): "admin",
    ("GET", "/api/v1/settings"): "admin",
    ("PATCH", "/api/v1/settings/ban-page"): "admin",
    ("PATCH", "/api/v1/settings/crowdsec-capi"): "admin",
    ("PATCH", "/api/v1/settings/crowdsec-hub"): "admin",
    ("PATCH", "/api/v1/settings/default-site"): "admin",
    ("GET", "/api/v1/settings/error-pages"): "admin",
    ("PUT", "/api/v1/settings/error-pages"): "admin",
    ("PATCH", "/api/v1/settings/llm"): "admin",
    ("POST", "/api/v1/settings/llm/test"): "admin",
    ("PATCH", "/api/v1/settings/smtp"): "admin",
    ("POST", "/api/v1/settings/smtp/test"): "admin",
    ("GET", "/api/v1/streams"): "member",
    ("POST", "/api/v1/streams"): "admin",
    ("DELETE", "/api/v1/streams/{stream_id}"): "admin",
    ("GET", "/api/v1/streams/{stream_id}"): "member",
    ("PATCH", "/api/v1/streams/{stream_id}"): "admin",
    ("POST", "/api/v1/tasks/sample"): "admin",
    ("GET", "/api/v1/tasks/{task_id}"): "member",
    ("GET", "/api/v1/upstreams"): "member",
    ("POST", "/api/v1/upstreams"): "admin",
    ("DELETE", "/api/v1/upstreams/{upstream_id}"): "admin",
    ("GET", "/api/v1/upstreams/{upstream_id}"): "member",
    ("PATCH", "/api/v1/upstreams/{upstream_id}"): "admin",
    ("POST", "/api/v1/upstreams/{upstream_id}/backends"): "admin",
    ("DELETE", "/api/v1/upstreams/{upstream_id}/backends/{backend_id}"): "admin",
    ("PATCH", "/api/v1/upstreams/{upstream_id}/backends/{backend_id}"): "admin",
    ("GET", "/api/v1/users"): "admin",
    ("POST", "/api/v1/users"): "admin",
    ("POST", "/api/v1/users/invite"): "admin",
    ("GET", "/api/v1/users/me"): "member",
    ("PATCH", "/api/v1/users/me"): "member",
    ("GET", "/api/v1/users/me/passkeys"): "member",
    ("POST", "/api/v1/users/me/passkeys"): "member",
    ("POST", "/api/v1/users/me/passkeys/options"): "member",
    ("POST", "/api/v1/users/me/passkeys/{passkey_id}/remove"): "member",
    ("PUT", "/api/v1/users/me/password"): "member",
    ("POST", "/api/v1/users/me/totp/disable"): "member",
    ("POST", "/api/v1/users/me/totp/enable"): "member",
    ("POST", "/api/v1/users/me/totp/recovery-codes"): "member",
    ("POST", "/api/v1/users/me/totp/setup"): "member",
    ("DELETE", "/api/v1/users/{user_id}"): "admin",
    ("PATCH", "/api/v1/users/{user_id}"): "admin",
    ("POST", "/api/v1/users/{user_id}/invite"): "admin",
    ("PUT", "/api/v1/users/{user_id}/password"): "admin",
    ("POST", "/api/v1/users/{user_id}/totp/disable"): "admin",
    ("GET", "/health"): "public",
}


def test_every_route_declares_a_role() -> None:
    """An endpoint nobody classified is an endpoint nobody secured."""
    undeclared = [
        (method, path) for method, path, _ in iter_routes(app) if (method, path) not in ROUTE_ROLES
    ]
    assert undeclared == [], (
        "These routes are not in ROUTE_ROLES. Add each one with the role it "
        "should demand, then make the guard match."
    )


def test_no_route_is_more_open_than_declared() -> None:
    wrong = {
        (method, path): (route_guard(route), ROUTE_ROLES[(method, path)])
        for method, path, route in iter_routes(app)
        if (method, path) in ROUTE_ROLES and route_guard(route) != ROUTE_ROLES[(method, path)]
    }
    assert wrong == {}, "actual != declared, as (actual, declared)"


def test_the_table_has_no_routes_the_app_lost() -> None:
    """A stale entry hides the fact that an endpoint was removed or renamed."""
    live = {(method, path) for method, path, _ in iter_routes(app)}
    assert set(ROUTE_ROLES) - live == set()


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_writes_are_admin_only(method: str) -> None:
    """The rule the whole feature rests on, stated once.

    The exceptions are deliberate and small: `/auth/*` is how you sign in, and
    `/users/me/*` is a member acting on their own row.
    """
    offenders = [
        path
        for (verb, path), role in ROUTE_ROLES.items()
        if verb == method
        and role != "admin"
        and not path.startswith("/api/v1/auth/")
        and not path.startswith("/api/v1/users/me")
    ]
    assert offenders == []
