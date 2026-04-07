"""Alembic 환경 설정."""

import os
from logging.config import fileConfig

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 환경변수에서 DB URL 오버라이드
db_url = os.getenv("AIP_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    url = config.get_main_option("sqlalchemy.url")
    connectable = create_engine(url)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
