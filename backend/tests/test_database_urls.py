"""The two database URLs must always name their driver.

A bare `postgresql://` means "whatever SQLAlchemy's default driver is", and
that default is not stable: SQLAlchemy 2.1 switched it from psycopg2 to
psycopg 3, which this project does not install. Migrations then failed with
"No module named 'psycopg'" -- on an upgrade nobody thought touched them.
"""

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    "given",
    [
        "postgres://u:p@db.example.com:5432/app",    # Heroku / Render style
        "postgresql://u:p@db.example.com:5432/app",  # the standard form
    ],
)
def test_a_provider_url_gets_explicit_drivers(given):
    settings = Settings(DATABASE_URL=given)
    assert settings.SQLALCHEMY_DATABASE_URI == "postgresql+asyncpg://u:p@db.example.com:5432/app"
    assert settings.SQLALCHEMY_SYNC_DATABASE_URI == "postgresql+psycopg2://u:p@db.example.com:5432/app"


def test_a_url_that_already_names_a_driver_is_left_alone():
    given = "postgresql+psycopg2://u:p@h:5432/app"
    assert Settings(DATABASE_URL=given).SQLALCHEMY_SYNC_DATABASE_URI == given


def test_the_component_settings_build_explicit_drivers():
    settings = Settings(
        DATABASE_URL="",
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p",
        POSTGRES_HOST="h",
        POSTGRES_PORT=5432,
        POSTGRES_DB="app",
    )
    assert settings.SQLALCHEMY_DATABASE_URI.startswith("postgresql+asyncpg://")
    assert settings.SQLALCHEMY_SYNC_DATABASE_URI == "postgresql+psycopg2://u:p@h:5432/app"
