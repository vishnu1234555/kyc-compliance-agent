import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PII_MODEL = "VK1402/AADHAAR_Extractor"


@dataclass(frozen=True)
class Settings:
    langsmith_api_key: str
    google_api_key: str
    langsmith_project: str
    # Hugging Face id or local folder of the fine-tuned GLiNER2 PII model; "off" runs regex only.
    pii_model: str
    pii_threshold: float


def get_settings() -> Settings:
    return Settings(
        langsmith_api_key=os.getenv("LANGSMITH_API_KEY", ""),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "kyc-compliance-agent"),
        pii_model=os.getenv("PII_MODEL", DEFAULT_PII_MODEL),
        pii_threshold=float(os.getenv("PII_THRESHOLD", "0.30")),
    )
