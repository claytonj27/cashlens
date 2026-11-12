import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides: object) -> Settings:
    # _env_file=None ignores any local .env so assertions are about code, not the machine.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_defaults_describe_local_development() -> None:
    settings = make()
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000


def test_log_level_is_normalized_to_upper_case() -> None:
    assert make(log_level=" debug ").log_level == "DEBUG"


@pytest.mark.parametrize("bad_level", ["verbose", "", "42"])
def test_unknown_log_level_is_rejected(bad_level: str) -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL must be one of"):
        make(log_level=bad_level)


@pytest.mark.parametrize("bad_port", [0, 65536])
def test_port_outside_tcp_range_is_rejected(bad_port: int) -> None:
    with pytest.raises(ValidationError):
        make(api_port=bad_port)


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(app_env="staging")


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("API_PORT", "8100")
    settings = make()
    assert settings.app_env == "test"
    assert settings.api_port == 8100


def test_features_default_on_outside_production_and_off_in_production() -> None:
    assert make().features_active is True
    assert make(app_env="preview").features_active is True
    assert make(app_env="production").features_active is False


def test_feature_switch_overrides_the_environment_default() -> None:
    assert make(app_env="production", features_enabled=True).features_active is True
    assert make(app_env="development", features_enabled=False).features_active is False
    assert make(app_env="production").integration_presence()["features_active"] is False
