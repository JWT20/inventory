from pydantic import field_validator
from pydantic_settings import BaseSettings

_INSECURE_DEFAULT = "INSECURE-DEV-KEY-CHANGE-IN-PRODUCTION"


class Settings(BaseSettings):
    database_url: str = _INSECURE_DEFAULT
    secret_key: str = _INSECURE_DEFAULT
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_extraction_model: str = "gemini-2.5-pro"  # stronger model for document extraction
    gemini_embedding_model: str = "gemini-embedding-001"
    # Fallback models tried when the primary keeps returning transient 5xx
    # ("high demand" 502/503 UNAVAILABLE). The fallback only fires on rare
    # spikes, so we go a tier UP where possible: a different model has its own
    # capacity pool (dodges the primary's overload) and better quality.
    # Empty string disables the fallback.
    #
    # Vision (scan/photo, the path that was 502-ing): 2.5-flash → 2.5-pro.
    # Extraction is already on the top model (2.5-pro); nothing outranks it, so
    # its fallback is a deliberate availability net on the lighter 2.5-flash —
    # a working extraction beats a hard 502 on a pakbon.
    gemini_vision_fallback_model: str = "gemini-2.5-pro"
    gemini_extraction_fallback_model: str = "gemini-2.5-flash"
    gemini_max_concurrent: int = 5  # max concurrent Gemini API requests
    gemini_extraction_max_dimension: int = 2048  # px – longest side for pakbon/invoice extraction (table digits need more resolution than box classification)
    match_threshold: float = 0.80
    ambiguity_margin: float = 0.05

    # Visual rerank (second pass over the vector search).
    #
    # Cosine similarity runs on the *text description* of a photo. Two boxes of
    # the same product line that differ in one printed word (a cultivar, a
    # cuvée) produce near-identical descriptions and therefore near-identical
    # embeddings — the ranking inside such a cluster is noise. The rerank pass
    # puts the scan photo next to the candidates' reference photos in a single
    # vision call and asks which one it actually is, which is the comparison the
    # embedding cannot make.
    # Runs on close calls only — rivals inside `ambiguity_margin`, or a best
    # catalogue match that is not open on the order. A match that leads its
    # runner-up by a wide margin is already decided; paying for a vision call to
    # confirm it buys nothing.
    rerank_enabled: bool = True
    # Candidates handed to the rerank call: those within `rerank_similarity_band`
    # of the best vector hit, capped at `rerank_max_candidates` SKUs and
    # `rerank_images_per_sku` reference photos each. Bounds the images per call
    # (and thus latency/cost) while keeping every plausible lookalike in view.
    rerank_max_candidates: int = 4
    rerank_similarity_band: float = 0.10
    rerank_images_per_sku: int = 2
    upload_dir: str = "/app/uploads"

    # Kafka
    kafka_bootstrap_servers: str = ""

    # Langfuse (LLM observability) — leave empty to disable
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Sentry (optional — leave empty to disable)
    sentry_dsn: str = ""

    # Redis (optional — used for rate limiting; empty = in-memory fallback)
    redis_url: str = ""

    # Storage backend ("local" or "s3")
    storage_backend: str = "local"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = ""
    s3_presigned_url_expiry: int = 3600  # seconds

    # Auth
    admin_password: str = _INSECURE_DEFAULT
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Standards-based Web Push. Empty keys disable the feature without affecting
    # the rest of the app. Generate one stable key pair per deployment.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""
    push_dispatch_interval_seconds: int = 5

    # Domain (used for CORS)
    domain: str = ""

    # Shopify (channel orders) — empty values disable the integration.
    # Provide as env secrets on the server; never commit real values.
    shopify_shop_domain: str = ""   # e.g. racesokken.myshopify.com
    # OAuth app credentials (Dev Dashboard → Settings → Credentials). The access
    # token is obtained via the OAuth install flow and encrypted per connection.
    shopify_api_key: str = ""       # Client ID
    shopify_api_secret: str = ""    # Client secret — OAuth token exchange + HMAC
    shopify_api_version: str = "2025-01"
    # Per-shop OAuth access tokens are encrypted in ChannelConnection. This
    # base64url-encoded 32-byte key stays outside the database so a database-only
    # leak never exposes usable Shopify credentials.
    channel_credential_encryption_key: str = ""
    # NB: access tokens remain per connection — never use a global token env var
    # (that would cross tenants).
    # Auto-sync: how often the background poller pulls new orders for every
    # connection in live-mode, in seconds. Default 180 (3 min) — fresh enough for
    # the warehouse floor without polling Shopify every minute; the manual "sync
    # now" button covers "I need it now". Set to 0 to disable (manual sync only).
    shopify_autosync_interval_seconds: int = 180

    # bol Retailer API — first, single-account integration. Credentials stay in
    # the server environment; the Admin connection binds that account to exactly
    # one organization and starts read-only in observe mode.
    bol_client_id: str = ""
    bol_client_secret: str = ""
    bol_token_url: str = "https://login.bol.com/token"
    bol_api_base_url: str = "https://api.bol.com/retailer"
    bol_demo_base_url: str = "https://api.bol.com/retailer-demo"

    # Veloyd shipping labels encode a track-and-trace value (for example V...)
    # rather than the visible Shopify order reference. The server uses this key
    # to resolve the barcode and verify its reference before shipping.
    veloyd_api_key: str = ""
    veloyd_api_base_url: str = "https://app.veloyd.nl/api"

    # Read-only stock feed for the wine advice app. The organization is bound
    # server-side so callers can never select a different merchant.
    advice_stock_api_key: str = ""
    advice_stock_organization_id: int | None = None
    # Hourly pull of the advice app's complete bottle-product snapshot. A
    # separate outbound key keeps read access in each direction independently
    # revocable. Empty base URL/key or interval 0 disables the puller.
    advice_products_base_url: str = ""
    advice_products_api_key: str = ""
    # Optional platform-level credential for protected Vercel previews. This is
    # separate from the advice app's own bearer key and normally stays empty in
    # production, where Inventory calls the public production domain.
    advice_products_vercel_bypass_secret: str = ""
    advice_products_sync_interval_seconds: int = 3600

    @property
    def advice_products_feed_configured(self) -> bool:
        """Whether manual product sync is safe to offer.

        The interval only controls the background loop. Keeping it out of this
        predicate allows installations to use manual-only sync with interval 0.
        """
        return bool(
            self.advice_products_base_url.strip()
            and self.advice_products_api_key
            and self.advice_stock_organization_id is not None
        )

    def has_advice_products_feed(self, organization_id: int | None) -> bool:
        """Whether this organization must use advice-owned bottle identities."""
        return bool(
            self.advice_products_feed_configured
            and organization_id == self.advice_stock_organization_id
        )

    @property
    def advice_products_sync_enabled(self) -> bool:
        """Whether the periodic product-sync loop should run."""
        return bool(
            self.advice_products_feed_configured
            and self.advice_products_sync_interval_seconds > 0
        )

    @property
    def push_enabled(self) -> bool:
        return bool(
            self.vapid_public_key
            and self.vapid_private_key
            and self.vapid_subject
            and self.push_dispatch_interval_seconds > 0
        )

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        if v == _INSECURE_DEFAULT:
            raise ValueError(
                "DATABASE_URL is not set. Provide a PostgreSQL connection string, e.g. postgresql://user:pass@host:5432/dbname"
            )
        return v

    @field_validator("secret_key")
    @classmethod
    def _check_secret_key(cls, v: str) -> str:
        if v == _INSECURE_DEFAULT:
            raise ValueError(
                "SECRET_KEY is not set. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @field_validator("admin_password")
    @classmethod
    def _check_admin_password(cls, v: str) -> str:
        if v == _INSECURE_DEFAULT:
            raise ValueError(
                "ADMIN_PASSWORD is not set. Choose a strong password and set it as an environment variable."
            )
        return v

    @field_validator("advice_stock_organization_id", mode="before")
    @classmethod
    def _empty_advice_stock_organization_id(cls, v: object) -> object:
        # Allows ADVICE_STOCK_ORGANIZATION_ID= in example/local env files while
        # retaining integer validation as soon as a value is configured.
        return None if v == "" else v

    class Config:
        env_file = ".env"


settings = Settings()
