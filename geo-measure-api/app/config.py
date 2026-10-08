"""Runtime configuration, read from environment variables."""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///data/app.db")
    )
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
    )
    # Zip-bomb guard: maximum total size of everything inside an uploaded zip.
    max_uncompressed_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_UNCOMPRESSED_MB", "300")) * 1024 * 1024
    )

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
