"""ORM models package.

Import every model module here so that ``Base.metadata`` is fully populated
before Alembic autogenerate or ``create_all`` runs. As feature tickets add
models (e.g. ``from app.models.project import Project``), register them below.
"""

from app.db.base import Base  # noqa: F401

# Feature models are imported here as they are added, for example:
# from app.models.project import Project  # noqa: F401

__all__ = ["Base"]
