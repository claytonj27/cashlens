"""Application settings.

Values come from environment variables and, for local development, from a
`.env` file in `services/api/` (environment variables win over the file).
Every field is validated when the settings object is created, so a bad value
fails at startup with a clear message instead of producing a misconfigured
server. Secret values use SecretStr so they never appear in logs or reprs;
presence is reported through `integration_presence()`, values never are.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

Environment = Literal["development", "test", "preview", "production"]


class Settings(BaseSettings):
    """Process configuration for the API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    app_env: Environment = "development"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    # Integrations. Optional until the milestone that needs them; the API
    # starts without any of them and reports only whether they are present.
    supabase_url: HttpUrl | None = None
    supabase_service_role_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openrouter_api_key: SecretStr | None = None

    # Operator-only diagnostics. Off by default; the routes answer 404 unless
    # enabled and the caller presents the token.
    ops_diagnostics_enabled: bool = False
    ops_diagnostics_token: SecretStr | None = None

    # Retention cleanup. Deliberately its own switch: OPS_DIAGNOSTICS_ENABLED gates the whole
    # diagnostics router, including a database probe and a paid model probe, so reusing it to
    # run cleanup would expose all of those at the same time. Off by default; the route answers
    # 404 unless both of these are set, and it is never a schedule on its own.
    retention_cleanup_enabled: bool = False
    retention_cleanup_token: SecretStr | None = None

    # PDF extraction runs in its own process. Wall clock, CPU and address space are enforced
    # per child; the decompression cap is passed to pypdf, which applies it while decompressing.
    # max_concurrent bounds how many run at once IN THIS INSTANCE and says nothing about others.
    extract_wall_clock_seconds: float = Field(default=60.0, gt=0, le=240)
    extract_cpu_soft_seconds: int = Field(default=45, ge=1, le=240)
    extract_cpu_hard_seconds: int = Field(default=55, ge=1, le=280)
    extract_address_space_bytes: int = Field(default=768 * 1024 * 1024, ge=64 * 1024 * 1024)
    extract_zlib_max_output_bytes: int = Field(default=16 * 1024 * 1024, ge=64 * 1024)
    extract_max_result_bytes: int = Field(default=8 * 1024 * 1024, ge=64 * 1024)
    extract_max_concurrent: int = Field(default=2, ge=1, le=16)
    extract_queue_seconds: float = Field(default=5.0, ge=0, le=60)

    # Upload quotas, counted per user across every business they belong to.
    quota_active_imports: int = Field(default=3, ge=1, le=50)
    quota_starts_per_hour: int = Field(default=10, ge=1, le=500)
    quota_starts_per_day: int = Field(default=40, ge=1, le=2000)
    quota_outstanding_bytes: int = Field(default=50 * 1024 * 1024, ge=1024 * 1024)

    # Product features: demo sessions, accounts, provisioning and workspace
    # reads. Unset means on everywhere except production, which stays a
    # health-only scaffold until an operator sets FEATURES_ENABLED=true there.
    # When off, every product route answers 404 before touching any service.
    features_enabled: bool | None = None

    # AI explanations. Off unless a deployment switches them on: every AI route answers 503
    # while this is false, so a deployment that holds no model credential cannot spend.
    ai_enabled: bool = False
    # `simulated` answers from the fact sheet itself so the interface can be seen working
    # without paying for anything. It is never a fallback and is refused in production.
    ai_provider: Literal["openai", "replay", "simulated"] = "openai"
    ai_simulated_outcome: Literal["valid", "invalid_evidence", "provider_error", "timeout"] = (
        "valid"
    )
    # The model is a setting, not an assumption: which one to run is decided by the comparison
    # in the M10 design's stage 3, and until then the deployment names one explicitly.
    ai_model: str | None = None
    # The server decides which models it will call at all. A model outside this list is
    # refused before any request is built, so a mistyped or costly id cannot be dispatched.
    ai_allowed_models: str = ""

    # OpenRouter: a second route, never the deployment's default and never a fallback.
    #
    # It exists so a reviewer can compare another model family on the synthetic demos. It is
    # deliberately NOT a value of `ai_provider`: making it one would let a deployment point
    # every business at it, and a private business's statements are not something to hand to a
    # variable set of upstream providers. The restriction is enforced in `routing.py` against
    # the business's own `data_mode`, which comes from the session, not from the request.
    #
    # When this is off, or when the caller does not ask for it, nothing about the existing
    # direct-OpenAI path changes.
    ai_openrouter_enabled: bool = False
    openrouter_model: str | None = None
    openrouter_allowed_models: str = ""
    # Sent as HTTP-Referer and X-Title. OpenRouter attributes requests with these; both are
    # public identifiers for this application and neither carries owner data.
    openrouter_site_url: str | None = None
    openrouter_app_title: str = "CashLens"

    ai_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ai_max_output_tokens: int = Field(default=1200, ge=64, le=8000)
    # How many unplaced rows one classification run asks about. Small on purpose: a batch is
    # one paid call, and a person has to read every answer it produces.
    ai_classify_batch_size: int = Field(default=8, ge=1, le=30)
    # Cash-flow classification asks about every row of a file rather than the few a rule could
    # not place, so its batch is sized by what the answer costs rather than by what a person can
    # read. Each item comes back as roughly fifty output tokens, so twenty-five of them sit
    # comfortably inside `ai_max_output_tokens` with room for a long reason on every one.
    ai_cashflow_batch_size: int = Field(default=25, ge=1, le=60)
    # How many of those batches may be in the air at once within a single request. Each is an
    # independent question about a disjoint set of rows, so this is latency and nothing else;
    # `ai_max_in_flight_per_identity` still decides how many reservations may stand together.
    ai_cashflow_concurrency: int = Field(default=4, ge=1, le=12)
    # When one request stops dispatching and reports its progress. Comfortably inside the
    # hosting platform's own request limit, because work abandoned by a killed process is work
    # this application cannot account for.
    ai_cashflow_deadline_seconds: float = Field(default=45.0, gt=0, le=280)
    # Spend caps in micro-dollars ($1 = 1,000,000), held in the database, not in a provider
    # dashboard: a dashboard limit is the provider's backstop, never this application's.
    ai_identity_daily_cost_cap_micro: int = Field(default=250_000, ge=0)
    ai_global_daily_cost_cap_micro: int = Field(default=2_000_000, ge=0)
    # The whole milestone's approved budget, counted with no time window at all. Daily caps
    # bound a day; only this bounds a milestone, because a daily cap resets by itself and an
    # approval does not. $25 approved for M10 across every provider, repair, grader and rerun.
    ai_milestone_total_cap_micro: int = Field(default=25_000_000, ge=0)
    # Reasoning models take an effort setting whose accepted values differ by model family
    # (gpt-5 takes `minimal`; gpt-5.1 and later take `none`), so it is named here rather than
    # guessed from the model id. Reasoning tokens are billed as output.
    ai_reasoning_effort: str | None = None
    ai_max_in_flight_per_identity: int = Field(default=1, ge=1, le=10)
    # How long an unsettled reservation keeps counting. Longer than any call can take, so a
    # process killed mid-call cannot quietly free budget it may already have spent.
    ai_reservation_expiry_seconds: int = Field(default=600, ge=60, le=3600)
    ai_recordings_dir: str | None = None

    # Operational dashboard. A comma-separated allowlist of Supabase auth user ids that may
    # read the AI usage dashboard. Empty by default, so no deployment grants it by accident;
    # an id is an opaque identifier, never a credential, and grants nothing but this one
    # read-only view. It is not membership in any business and opens no financial data.
    # A deployment that publishes the synthetic demo and nothing else.
    #
    # It exists because the public production domain shares a database with private businesses.
    # Switching this on refuses every private path and every workspace that is not a demo, in
    # the API rather than only in the interface, so a public deployment cannot reach a person's
    # bank data even though its credentials could technically read the table. Off by default:
    # a deployment is only ever restricted deliberately.
    public_demo_only: bool = False

    admin_user_ids: str = ""
    # Spend that happened outside this application's ledger and so is not bounded by the
    # database caps. Reported separately rather than folded into the enforced total.
    #
    # The default is the one probe this project actually made, $0.001252, measured and recorded
    # in the milestone notes. It is a published figure rather than a secret, so carrying it in
    # code means one fewer thing to configure and one fewer way for the dashboard's overall
    # total to be quietly wrong. Set it to 0 on a deployment that made no such call.
    external_probe_cost_micro: int = 1252

    # The Discovery portal (ADR 0014): an internal customer-specific workspace inside
    # CashLens. Off by default, so a deployment carries it only when opted in deliberately, and
    # the public synthetic-only deployment never does: `refuse_on_public_demo` refuses every
    # discovery route before this switch is even consulted. Roles come from `public.profiles`,
    # read by Python on every request; this switch says nothing about who is what.
    discovery_enabled: bool = False
    # Whether an account may self-assign a template. Off: assignments come from the seed.
    discovery_self_assign: bool = False
    # Fallback template slug for a self-assigned or throwaway account, as the thin vertical slice
    # uses. The named stakeholders are assigned per person by the seed script, not from this.
    discovery_default_template: str = "founder-lead"

    @property
    def admin_ids(self) -> frozenset[str]:
        """The user ids allowed to read the operational dashboard, lowercased and trimmed."""
        return frozenset(
            part.strip().lower() for part in self.admin_user_ids.split(",") if part.strip()
        )

    @property
    def ai_model_allowlist(self) -> tuple[str, ...]:
        """Models this deployment may call. The configured model is always included."""
        listed = [m.strip() for m in self.ai_allowed_models.split(",") if m.strip()]
        if self.ai_model and self.ai_model not in listed:
            listed.append(self.ai_model)
        return tuple(listed)

    @property
    def openrouter_model_allowlist(self) -> tuple[str, ...]:
        """Models this deployment may call through OpenRouter, its own list.

        Separate from `ai_model_allowlist` on purpose: the two routes bill differently, price
        differently and are permitted on different data, so one shared list would let a model
        approved for the direct route be reached through the other.
        """
        listed = [m.strip() for m in self.openrouter_allowed_models.split(",") if m.strip()]
        if self.openrouter_model and self.openrouter_model not in listed:
            listed.append(self.openrouter_model)
        return tuple(listed)

    @property
    def features_active(self) -> bool:
        """Whether product routes exist in this deployment."""
        if self.features_enabled is not None:
            return self.features_enabled
        return self.app_env != "production"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        level = str(value).strip().upper()
        if level not in LOG_LEVELS:
            allowed = ", ".join(LOG_LEVELS)
            raise ValueError(f"LOG_LEVEL must be one of {allowed}; got {value!r}")
        return level

    def integration_presence(self) -> dict[str, bool]:
        """Which optional settings are configured. Never the values."""
        return {
            "supabase_url": self.supabase_url is not None,
            "supabase_service_role_key": self.supabase_service_role_key is not None,
            "openai_api_key": self.openai_api_key is not None,
            "openai_model": self.openai_model is not None,
            "ops_diagnostics_enabled": self.ops_diagnostics_enabled,
            "ops_diagnostics_token": self.ops_diagnostics_token is not None,
            "retention_cleanup_enabled": self.retention_cleanup_enabled,
            "retention_cleanup_token": self.retention_cleanup_token is not None,
            "features_active": self.features_active,
            "ai_enabled": self.ai_enabled,
            "ai_model": self.ai_model is not None,
            "ai_openrouter_enabled": self.ai_openrouter_enabled,
            "openrouter_api_key": self.openrouter_api_key is not None,
            "openrouter_model": self.openrouter_model is not None,
            "discovery_enabled": self.discovery_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the validated settings, created once per process."""
    return Settings()
