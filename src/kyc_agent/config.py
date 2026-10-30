import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PII_MODEL = "VK1402/AADHAAR_Extractor"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RBI_PDF = REPO_ROOT / "data" / "rbi_nbfc_kyc_directions_2025_from_html.pdf"
DEFAULT_CHROMA_DIR = REPO_ROOT / "data" / "chroma"


@dataclass(frozen=True)
class Settings:
    langsmith_api_key: str
    google_api_key: str
    langsmith_project: str
    # Hugging Face id or local folder of the fine-tuned GLiNER2 PII model; "off" runs regex only.
    pii_model: str
    pii_threshold: float
    # retrieve_rules: the Directions PDF, the Chroma folder, the Gemini embedding model, chunks per query
    rbi_pdf_path: str
    chroma_dir: str
    embedding_model: str
    retrieval_k: int
    # decide: "rules" (baseline only, no LLM), "llm" (Gemini Flash, structured output) or "agent" (tool loop)
    decide_mode: str
    gemini_model: str
    gemini_thinking_budget: int | None
    # human_review: SQLite file for checkpoints so a reviewer can resume later; empty = in memory
    checkpoint_db: str


def get_settings() -> Settings:
    return Settings(
        langsmith_api_key=os.getenv("LANGSMITH_API_KEY", ""),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "kyc-compliance-agent"),
        pii_model=os.getenv("PII_MODEL", DEFAULT_PII_MODEL),
        pii_threshold=float(os.getenv("PII_THRESHOLD", "0.30")),
        rbi_pdf_path=os.getenv("RBI_PDF_PATH", str(DEFAULT_RBI_PDF)),
        chroma_dir=os.getenv("CHROMA_DIR", str(DEFAULT_CHROMA_DIR)),
        embedding_model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        retrieval_k=int(os.getenv("RETRIEVAL_K", "3")),
        decide_mode=os.getenv("DECIDE_MODE", "llm"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        gemini_thinking_budget=_optional_int(os.getenv("GEMINI_THINKING_BUDGET", "0")),
        checkpoint_db=os.getenv("CHECKPOINT_DB", ""),
    )


def _optional_int(value: str) -> int | None:
    """"0" -> 0 (thinking off), "" or "none" -> None (the model's default)."""
    return None if value.strip().lower() in ("", "none") else int(value)
