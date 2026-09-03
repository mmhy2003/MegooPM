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
from app.models.auth_token import AuthToken  # noqa: F401
from app.models.certificate import Certificate  # noqa: F401
from app.models.cluster_state import (  # noqa: F401
    ClusterNode,
    ClusterState,
    ClusterSweep,
)
from app.models.crowdsec import CrowdSecCredential  # noqa: F401
from app.models.crowdsec_job_run import CrowdSecJobRun  # noqa: F401
from app.models.crowdsec_whitelist import (  # noqa: F401
    CrowdSecWhitelist,
    CrowdSecWhitelistApply,
)
from app.models.custom_page import CustomPage  # noqa: F401
from app.models.dead_host import DeadHost  # noqa: F401
from app.models.dns_credential import DnsProviderCredential  # noqa: F401
from app.models.instance_settings import InstanceSettings  # noqa: F401
from app.models.node_metrics import NodeMetrics  # noqa: F401
from app.models.passkey import Passkey  # noqa: F401
from app.models.proxy_host import ProxyHost, ProxyHostLocation  # noqa: F401
from app.models.recovery_code import RecoveryCode  # noqa: F401
from app.models.redirection_host import RedirectionHost  # noqa: F401
from app.models.stream import Stream  # noqa: F401
from app.models.upstream import Upstream, UpstreamBackend  # noqa: F401
from app.models.user import User, UserRole  # noqa: F401
from app.models.visitor_day import VisitorDay  # noqa: F401

__all__ = [
    "Base",
    "AccessList",
    "AccessListAuth",
    "AccessListClient",
    "AuditLog",
    "AuthToken",
    "Certificate",
    "ClusterNode",
    "ClusterState",
    "ClusterSweep",
    "CrowdSecCredential",
    "CrowdSecJobRun",
    "CrowdSecWhitelist",
    "CrowdSecWhitelistApply",
    "CustomPage",
    "DeadHost",
    "DnsProviderCredential",
    "InstanceSettings",
    "NodeMetrics",
    "Passkey",
    "VisitorDay",
    "ProxyHost",
    "ProxyHostLocation",
    "RecoveryCode",
    "RedirectionHost",
    "Stream",
    "Upstream",
    "UpstreamBackend",
    "User",
    "UserRole",
]
