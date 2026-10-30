"""Reader 2 of 2: Vishnu's fine-tuned GLiNER2 PII model (Hugging Face VK1402/AADHAAR_Extractor).

The model is asked for what regex cannot read: the person's name and the address. It is also a
second reader for the ID numbers. Facts about it that shaped this file:

- Labels must be Title Case or a {label: description} dict; bare SNAKE_CASE returns nothing.
- It was trained on financial IDs only: no date-of-birth or PIN-code label, so those stay
  with the regex reader.
- It finds spans reliably but picks the wrong label when no field name is printed next to
  the value, which is why every candidate goes through arbitration.py afterwards. On the eval
  set its "Address" label fires on name spans and never on an address.
- Vishnu's own sweep put the best threshold at 0.99 for the ID labels. On the KYC eval set the
  Title Case names on Aadhaar cards score 0.3-0.8, so the default here is 0.30
  (evals/sweep_threshold.py: 275/279 fields at 0.2-0.3, 274 at 0.4-0.7, 272 at 0.99).

Only the evals load the real model. Tests use FakeReader or NullReader.
"""

import logging
from functools import lru_cache
from typing import Any, Protocol

from kyc_agent.arbitration import Candidate
from kyc_agent.config import get_settings

logger = logging.getLogger(__name__)

LABELS: dict[str, str] = {
    "Person Name": "full name of the person the document belongs to, as printed on the document",
    "Address": "postal address printed on the document",
    "Aadhaar Number": "12-digit Aadhaar number, printed in full or masked like XXXX XXXX 1234",
    "PAN Number": "10-character Permanent Account Number such as ABCPE1234F",
}


class PIIReader(Protocol):
    name: str

    def read(self, text: str) -> list[Candidate]:
        """Candidates with offsets into `text`."""


class NullReader:
    """The model switched off: regex only."""

    name = "off"

    def read(self, text: str) -> list[Candidate]:
        return []


class FakeReader:
    """For tests: reports the (label, value, confidence) triples it was given, where they occur in the text."""

    name = "fake"

    def __init__(self, spans: list[tuple[str, str, float]]) -> None:
        self.spans = spans

    def read(self, text: str) -> list[Candidate]:
        return [cand for label, value, confidence in self.spans for cand in locate(text, label, value, confidence)]


def locate(text: str, label: str, value: str, confidence: float | None) -> list[Candidate]:
    """A candidate at the first place `value` occurs in `text`, or nothing when it does not."""
    start = text.find(value)
    if start < 0 or not value:
        return []
    return [{"label": label, "text": value, "start": start, "end": start + len(value),
             "confidence": confidence, "source": "model", "trusted": False}]


def parse_model_output(raw: Any, text: str) -> list[Candidate]:
    """GLiNER2 output -> candidates. Accepts {label: [...]} with strings or dicts, or a flat list."""
    entities = raw.get("entities", raw) if isinstance(raw, dict) else raw
    out: list[Candidate] = []

    def add(label: str, match: Any) -> None:
        if isinstance(match, str):
            out.extend(locate(text, label, match.strip(), None))
            return
        if not isinstance(match, dict):
            return
        value = str(match.get("text", "")).strip()
        if not value:
            return
        start = match.get("char_start", match.get("start"))
        end = match.get("char_end", match.get("end"))
        confidence = match.get("confidence", match.get("score"))
        confidence = float(confidence) if confidence is not None else None
        if isinstance(start, int) and isinstance(end, int) and text[start:end].strip() == value:
            out.append({"label": label, "text": value, "start": start + (len(text[start:end]) - len(text[start:end].lstrip())),
                        "end": start + len(text[start:end].rstrip()), "confidence": confidence,
                        "source": "model", "trusted": False})
        else:
            out.extend(locate(text, label, value, confidence))

    if isinstance(entities, dict):
        for label, matches in entities.items():
            for match in matches or []:
                add(str(label), match)
    elif isinstance(entities, list):
        for match in entities:
            if isinstance(match, dict):
                add(str(match.get("label", "Unknown")), match)
    return out


class GLiNERReader:
    """Lazy wrapper: the 800 MB model loads on the first read, on CPU."""

    def __init__(self, model_id: str, threshold: float, labels: dict[str, str] | None = None) -> None:
        self.name = model_id
        self.threshold = threshold
        self.labels = labels or LABELS
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            logger.info("loading PII model %s", self.name)
            self._model = load_gliner2(self.name)
        return self._model

    def read(self, text: str) -> list[Candidate]:
        raw = self._load().extract_entities(
            text, self.labels, threshold=self.threshold, include_confidence=True, include_spans=True
        )
        candidates = parse_model_output(raw, text)
        logger.debug("PII model: %d candidates at threshold %.2f", len(candidates), self.threshold)
        return candidates


def load_gliner2(model_id: str) -> Any:
    """GLiNER2.from_pretrained on CPU, with one shim for this checkpoint's tokenizer metadata.

    The checkpoint was saved with transformers 5, which writes ``tokenizer_class:
    TokenizersBackend`` and a list under ``extra_special_tokens``. gliner2 2.0.0 pins
    transformers 4, whose AutoTokenizer does not know that class name. The tokenizer itself is
    the DeBERTa-v3 fast tokenizer in tokenizer.json, so when the class lookup fails we load it
    as that, and let gliner2 register its own special tokens afterwards as it always does.
    """
    # Imported here so the unit tests never need torch.
    import gliner2.models.base as gliner_base
    import gliner2.models.span.model as span_model
    from gliner2 import GLiNER2
    from transformers import DebertaV2TokenizerFast

    original = gliner_base.load_extractor_tokenizer

    def load_tokenizer(repo_or_dir: str) -> Any:
        try:
            return original(repo_or_dir)
        except ValueError as exc:
            if "TokenizersBackend" not in str(exc):
                raise
            logger.info("tokenizer metadata is from transformers 5; loading tokenizer.json as DeBERTa-v3")
            return DebertaV2TokenizerFast.from_pretrained(repo_or_dir, extra_special_tokens={})

    gliner_base.load_extractor_tokenizer = load_tokenizer
    span_model.load_extractor_tokenizer = load_tokenizer  # the span model imported the name directly
    return GLiNER2.from_pretrained(model_id, map_location="cpu")


@lru_cache(maxsize=1)
def default_reader() -> PIIReader:
    """The reader the extract node uses: the model from settings, or NullReader when PII_MODEL=off."""
    settings = get_settings()
    if settings.pii_model.strip().lower() in ("", "off", "none"):
        return NullReader()
    return GLiNERReader(settings.pii_model, settings.pii_threshold)
