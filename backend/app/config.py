from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    GOOGLE_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    DATABASE_URL: str = "postgresql+asyncpg://amia:amia@localhost:5432/amia"
    CHROMA_PERSIST_DIR: str = "./chroma_data"
    VISION_MODEL: str = "gemini-2.5-pro"
    REASONING_MODEL: str = "gemini-2.5-pro"
    UPLOAD_DIR: str = "./uploads"

    # OCR Configuration
    USE_CNN_OCR: bool = True  # Enable/disable CNN-based OCR (EasyOCR)
    CNN_OCR_CONSENSUS_THRESHOLD: int = 2  # 2/3 methods must agree
    CNN_OCR_MIN_CONFIDENCE: float = 0.7  # Minimum confidence for CNN results

    model_config = {
        "env_file": str(Path(__file__).resolve().parent.parent / ".env"),
        "env_file_encoding": "utf-8",
    }

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sync_database_url(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "")


settings = Settings()
