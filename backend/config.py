import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class Settings(BaseModel):
    mode: Literal["mock", "real"] = "mock"
    database_url: str = "sqlite:///./instance/lost_and_found_mock.db"
    staff_token: str = "local-mock-only"
    ollama_url: str = "http://127.0.0.1:11434"
    vlm_model: str = "qwen3-vl:8b"
    verification_vlm_model: str | None = None
    llm_model: str = "qwen3-vl:8b"
    model_timeout: int = Field(default=180, ge=5, le=600)
    max_reextractions: int = Field(default=2, ge=0, le=2)
    max_questions: int = Field(default=2, ge=0, le=2)
    require_official_category: bool = False

    @model_validator(mode="after")
    def local_model_and_staff_key(self):
        parsed = urlsplit(self.ollama_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("OLLAMA_URL must be a local HTTP origin")
        if not self.staff_token.isascii() or not self.staff_token.strip():
            raise ValueError("STAFF_TOKEN must be a nonempty ASCII token")
        if self.mode == "real" and (len(self.staff_token) < 32 or self.staff_token == "local-mock-only"):
            raise ValueError("Real Mode requires a unique STAFF_TOKEN of at least 32 characters")
        if self.mode == "real" and self.database_url.endswith("lost_and_found_mock.db"):
            raise ValueError("Real Mode must use a separate database")
        if any(re.search(r"[-_:]cloud(?:$|[-_:/])", model.lower())
               for model in (self.vlm_model, self.verification_vlm_model or "", self.llm_model)):
            raise ValueError("Cloud model tags are not supported. Use models installed in local Ollama.")
        return self

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        default = cls()
        mode = os.getenv("APP_MODE")
        if not mode:
            raise ValueError("Set APP_MODE=mock or APP_MODE=real in .env (see .env.example).")
        return cls(
            mode=mode.strip().lower(),
            database_url=os.getenv("DATABASE_URL", default.database_url),
            staff_token=os.getenv("STAFF_TOKEN", default.staff_token),
            ollama_url=os.getenv("OLLAMA_URL", default.ollama_url),
            vlm_model=os.getenv("VLM_MODEL", default.vlm_model),
            verification_vlm_model=os.getenv("VERIFICATION_VLM_MODEL") or None,
            llm_model=os.getenv("LLM_MODEL", default.llm_model),
            model_timeout=os.getenv("MODEL_TIMEOUT", default.model_timeout),
            max_reextractions=os.getenv("MAX_REEXTRACTIONS", default.max_reextractions),
            max_questions=os.getenv("MAX_QUESTIONS", default.max_questions),
            require_official_category=os.getenv("REQUIRE_OFFICIAL_CATEGORY", default.require_official_category),
        )
