"""
test_cors_origins.py
====================
WHAT THIS FILE TESTS
--------------------
`Settings.cors_origins` -- the list of websites a browser may call the API from.

WHY IT EXISTS
-------------
A deployed frontend whose address is missing from this list loads fine but
cannot make a single API call: the browser blocks every request, so sign-in,
the dashboard and chat all fail. That happened on the live demo when the
address was entered with a trailing slash, which a browser never sends.

TIER 1: pure configuration. No database, no network.
"""

from app.core.config import Settings


def test_trailing_slashes_and_spaces_are_ignored():
    settings = Settings(FRONTEND_ORIGIN=" https://a.vercel.app/ ,https://b.app", FRONTEND_URL="")

    assert "https://a.vercel.app" in settings.cors_origins
    assert "https://b.app" in settings.cors_origins


def test_frontend_url_alone_is_enough():
    settings = Settings(FRONTEND_ORIGIN="", FRONTEND_URL="https://c.app/")

    assert "https://c.app" in settings.cors_origins


def test_no_deployed_frontend_leaves_only_local_development():
    settings = Settings(FRONTEND_ORIGIN="", FRONTEND_URL="")

    assert settings.cors_origins == list(settings.BACKEND_CORS_ORIGINS)
