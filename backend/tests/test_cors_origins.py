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
