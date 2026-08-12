from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """Always the HonestSpend default folder (never the old financial-os path)."""
    return Path.home() / ".HonestSpend"


def legacy_data_dir() -> Path:
    """Pre-1.0.56 location — still readable if books live there."""
    return Path.home() / ".financial-os"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FOS_", env_file=".env", extra="ignore")

    app_name: str = "HonestSpend"
    # Seed fake demo accounts only when explicitly requested
    seed_demo: bool = False
    host: str = "127.0.0.1"
    port: int = 7420
    # Default DB under user home / OneDrive-friendly path can be overridden
    data_dir: Path = default_data_dir()
    database_url: str | None = None
    # Security: force X-API-Key on all API routes (except health)
    require_api_key: bool = False
    # If host is not loopback and this is false, startup refuses to bind (unless require_api_key)
    allow_non_loopback: bool = False

    # Grok / xAI (optional — rules work offline without this)
    xai_api_key: str | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model: str = "grok-4-1-fast-reasoning"
    categorizer_min_confidence: float = 0.55
    auto_apply_min_confidence: float = 0.85

    # Plaid (optional — CSV import works without this)
    plaid_client_id: str | None = None
    plaid_secret: str | None = None
    plaid_env: str = "sandbox"  # sandbox | development | production

    # Commercial license (see docs/LICENSING.md). Default: OSS unlocked.
    # Store/MSIX packaged client sets FOS_LICENSE_ENFORCE=1 when launching the engine.
    license_enforce: bool = False
    license_grace_days: int = 90
    license_server_url: str | None = None
    license_allow_dev_keys: bool = False  # set true in CI; enforce=false also allows dev keys
    # store | unpackaged | sideload | dev — set by WinUI BackendHost when known
    license_distribution: str | None = None

    @property
    def plaid_enabled(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)

    @property
    def plaid_base_url(self) -> str:
        env = (self.plaid_env or "sandbox").lower()
        if env == "production":
            return "https://production.plaid.com"
        if env == "development":
            return "https://development.plaid.com"
        return "https://sandbox.plaid.com"

    @property
    def db_path(self) -> Path:
        """Prefer honestspend.db; fall back to legacy financial_os.db if present."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        modern = self.data_dir / "honestspend.db"
        legacy = self.data_dir / "financial_os.db"
        if modern.is_file():
            return modern
        if legacy.is_file():
            return legacy
        return modern

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def grok_enabled(self) -> bool:
        return bool(self.xai_api_key)

    @property
    def is_loopback_host(self) -> bool:
        h = (self.host or "").strip().lower()
        return h in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")

    @property
    def effective_require_api_key(self) -> bool:
        if self.require_api_key:
            return True
        # Non-loopback without explicit allow → require key
        if not self.is_loopback_host and not self.allow_non_loopback:
            return True
        return False


settings = Settings()
