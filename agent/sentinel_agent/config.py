import socket
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    host_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=128)
    host_name: str = Field(default_factory=socket.gethostname, min_length=1, max_length=128)
    environment: Literal["development", "test", "demo", "production"] = "development"
    agent_port: int = Field(default=9101, ge=1, le=65535)
    agent_bind_address: str = "127.0.0.1"
    agent_sample_seconds: float = Field(default=5, ge=0.1, le=300)
    agent_disk_path: str = "/"
    agent_procfs_path: str | None = None
    agent_identity_path: Path | None = None
    agent_reporting_seconds: int = Field(default=30, ge=5, le=300)
