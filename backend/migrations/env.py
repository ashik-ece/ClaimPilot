from alembic import context
from sqlalchemy import create_engine

from app.config import settings

config = context.config


def run_migrations_online() -> None:
    # Migrations run as the schema owner, never as the restricted application role.
    url = config.attributes.get("url") or settings.migration_database_url
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
