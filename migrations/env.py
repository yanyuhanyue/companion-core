from alembic import context
from sqlalchemy import engine_from_config, pool

engine = engine_from_config(
    context.config.get_section(context.config.config_ini_section),
    prefix="sqlalchemy.",
    poolclass=pool.NullPool,
)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
