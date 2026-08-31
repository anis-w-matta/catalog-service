from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    # Shared-secret gate for backend -> catalog-service calls, same
    # require_api_key pattern the backend itself already uses for its own
    # callers. Off by default so local dev keeps working with no setup.
    api_key: str | None = None

    fuzzy_accept: float = 0.85
    fuzzy_suggest: float = 0.60
    fuzzy_alias_threshold: int = 80
    resolver_tie_epsilon: float = 0.02
    attribute_conflict_penalty: float = 0.35

    customer_match_threshold: float = 75.0
    customer_match_tie_margin: float = 5.0
    item_fuzzy_threshold: float = 75.0
    item_ambiguity_margin: float = 5.0
    top_k_candidates: int = 10
    numeric_conflict_penalty: float = 40.0


settings = Settings()
