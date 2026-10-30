import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    langsmith_api_key: str
    google_api_key: str
    langsmith_project: str


def get_settings() -> Settings:
    return Settings(
        langsmith_api_key=os.getenv("LANGSMITH_API_KEY", ""),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "kyc-compliance-agent"),
    )
