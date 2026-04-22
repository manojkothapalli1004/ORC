"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # LLM keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    router_api_key: str = ""

    # Provider role mapping
    reviewer_provider: str = "openai"
    reviewer_mode: str = "direct"
    reviewer_model: str = "gpt-4o"
    planner_provider: str = "openai"
    planner_mode: str = "direct"
    planner_model: str = "gpt-4o"
    builder_provider: str = "anthropic"
    builder_mode: str = "direct"
    builder_model: str = "claude-sonnet-4-20250514"

    # Workflow execution mode defaults
    default_workflow_mode: str = "normal"
    compact_reviewer_model: str = "gpt-4o-mini"
    compact_planner_model: str = "gpt-4o-mini"
    compact_builder_model: str = "claude-3-5-haiku-latest"
    rich_reviewer_model: str = "gpt-4o"
    rich_planner_model: str = "gpt-4o"
    rich_builder_model: str = "claude-sonnet-4-20250514"
    go_wild_reviewer_model: str = "gpt-4.1"
    go_wild_planner_model: str = "gpt-4.1"
    go_wild_builder_model: str = "claude-opus-4-1-20250805"

    # Assistant Brain provider: "auto" | "openai" | "anthropic" | "claude_code_local" | "mock"
    assistant_provider: str = "auto"
    assistant_model_openai: str = "gpt-4o"
    assistant_model_anthropic: str = "claude-sonnet-4-20250514"
    assistant_model_claude_code_local: str = "opus"

    # Router / OpenAI-compatible backend
    router_base_url: str = ""
    router_provider_name: str = "router"
    router_timeout_seconds: int = 60

    # Approval limits
    max_files_per_batch: int = 5
    max_tokens_per_response: int = 8000

    # Server
    host: str = "127.0.0.1"
    port: int = 8100
    local_network_only: bool = True
    local_access_username: str = "operator"
    local_access_password: str = ""

    # Storage
    state_dir: Path = Path("./data")
    log_dir: Path = Path("./logs")
    builder_job_dir: Path = Path("./bridge/builder_jobs")
    session_dir: Path = Path("./data/sessions")
    idea_thread_dir: Path = Path("./data/ideas")
    prompt_template_dir: Path = Path("./data/prompts")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator(
        "state_dir",
        "log_dir",
        "builder_job_dir",
        "session_dir",
        "idea_thread_dir",
        "prompt_template_dir",
        mode="after",
    )
    @classmethod
    def _anchor_relative_paths(cls, value: Path) -> Path:
        return value if value.is_absolute() else (_PROJECT_ROOT / value).resolve()


settings = Settings()
