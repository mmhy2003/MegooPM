"""ORM models package.

Import every model module here so that ``Base.metadata`` is fully populated
before Alembic autogenerate or ``create_all`` runs.
"""

from app.db.base import Base  # noqa: F401
from app.models.access_list import (  # noqa: F401
    AccessList,
    AccessListAuth,
    AccessListClient,
)
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.certificate import Certificate  # noqa: F401
from app.models.cluster_state import ClusterNode, ClusterState  # noqa: F401
from app.models.crowdsec import CrowdSecCredential  # noqa: F401
from app.models.dead_host import DeadHost  # noqa: F401
from app.models.dns_credential import DnsProviderCredential  # noqa: F401
from app.models.proxy_host import ProxyHost, ProxyHostLocation  # noqa: F401
from app.models.redirection_host import RedirectionHost  # noqa: F401
from app.models.stream import Stream  # noqa: F401
from app.models.upstream import Upstream, UpstreamBackend  # noqa: F401
from app.models.user import User, UserRole  # noqa: F401

__all__ = [
    "Base",
    "AccessList",
    "AccessListAuth",
    "AccessListClient",
    "AuditLog",
    "Certificate",
    "ClusterNode",
    "ClusterState",
    "CrowdSecCredential",
    "DeadHost",
    "DnsProviderCredential",
    "ProxyHost",
    "ProxyHostLocation",
    "RedirectionHost",
    "Stream",
    "Upstream",
    "UpstreamBackend",
    "User",
    "UserRole",
]
