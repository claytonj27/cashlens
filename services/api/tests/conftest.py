import pytest

from app.config import Settings, get_settings

CONFIG_VARS = (
    "APP_ENV",
    "LOG_LEVEL",
    "API_HOST",
    "API_PORT",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_ALLOWED_MODELS",
    "AI_OPENROUTER_ENABLED",
    "OPS_DIAGNOSTICS_ENABLED",
    "OPS_DIAGNOSTICS_TOKEN",
    "FEATURES_ENABLED",
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch):
    """Keep tests independent of the developer's shell environment."""
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    # Ignore the developer's local .env so no test can reach a real service.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    import app.supabase as supabase_module

    supabase_module._client = None
    yield
    get_settings.cache_clear()
    supabase_module._client = None


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):
    """The in-memory data service and AI settings both AI test modules drive.

    It lives here rather than in one of them so the other does not have to import a fixture,
    which would shadow every `db` parameter in the file that did.
    """
    import httpx

    from app.main import app
    from app.supabase import SupabaseClient, get_supabase
    from tests.test_ai_brief import FakeDb

    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_MODEL", "test-model")
    get_settings.cache_clear()
    fake = FakeDb()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()
    get_settings.cache_clear()
