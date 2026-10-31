"""The RBI NBFC KYC Directions, 2025 as a searchable index. LangChain components only.

PyPDFLoader reads data/*.pdf page by page. The pages are then cut by paragraph number first,
so every chunk knows which paragraph, sub-paragraph, clause or Explanation it comes from and
carries the citation string evals/POLICY.md uses ("para 42(1)", "para 5(1)(xiv)", "para 23
Explanation 1"). A sub-paragraph chunk starts with its paragraph's opening sentence, so
"(5) the PAN or ... Form No. 60" is embedded together with "23. For undertaking CDD, the NBFC
shall obtain the following from an individual". RecursiveCharacterTextSplitter then cuts only
the long paragraphs, keeping that metadata. GoogleGenerativeAIEmbeddings + langchain_chroma store the chunks on disk
(data/chroma/, gitignored) and are rebuilt when the folder is missing.
"""

import json
import logging
import re
import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from langchain_core.documents import Document

from kyc_agent.backoff import with_backoff
from kyc_agent.config import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "rbi_nbfc_kyc_directions_2025"

_PARA = re.compile(r"^(\d{1,3})\.\s+(?=\S)")  # "42. Updation / Periodic Updation of KYC"
_SUB = re.compile(r"^\((\d{1,2})\)\s+(?=\S)")  # "(1) The NBFC shall ..."
_CLAUSE = re.compile(r"^\(([ivxl]{1,6})\)\s+(?=\S)")  # "(xiv) 'Officially Valid Document' ..."
_EXPLANATION = re.compile(r"^Explanation(?:\s+(\d+))?\s*:")  # "Explanation 1: ..." (numbered ones split)


def load_pages(pdf_path: Path) -> list[Document]:
    """One Document per page, metadata {"source", "page"} (PyPDFLoader's own shape)."""
    from langchain_community.document_loaders import PyPDFLoader

    pages = PyPDFLoader(str(pdf_path)).load()
    logger.info("loaded %d pages from %s", len(pages), pdf_path.name)
    return pages


def citation_for(para: str, sub: str = "", clause: str = "", explanation: str = "") -> str:
    """The evals/POLICY.md spelling of a reference."""
    if explanation:
        return f"para {para} Explanation {explanation}"
    text = f"para {para}"
    if sub:
        text += f"({sub})"
    if clause:
        text += f"({clause})"
    return text


def split_by_paragraph(pages: list[Document]) -> list[Document]:
    """Cut the Directions at every paragraph number, sub-paragraph, roman clause and Explanation.

    Numbers must increase by one to start a new chunk (1., 2., 3., ... and (1), (2), ...), so a
    stray "10. " at a line break inside a sentence does not open paragraph 10. Text before
    paragraph 1 (title, table of contents, introduction) is dropped: it is never cited.
    """
    lines: list[tuple[str, int]] = []
    for page in pages:
        page_number = int(page.metadata.get("page", 0)) + 1
        for line in page.page_content.split("\n"):
            lines.append((line.rstrip(), page_number))

    chunks: list[Document] = []
    para = sub = clause = explanation = ""
    heading = ""
    lead = ""  # the paragraph's own opening text, prefixed to its sub-chunks so "(5) the PAN ..." keeps its context
    buffer: list[str] = []
    start_page = 0
    roman_order = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv",
                   "xvi", "xvii", "xviii", "xix", "xx"]

    def flush() -> None:
        nonlocal lead
        text = "\n".join(buffer).strip()
        if para and text:
            if sub or clause or explanation:
                text = f"{lead}\n{text}" if lead else text
            else:
                lead = " ".join(text.split())[:300]
            chunks.append(Document(
                page_content=text,
                metadata={
                    "para": para, "sub": sub, "clause": clause, "explanation": explanation,
                    "citation": citation_for(para, sub, clause, explanation),
                    "heading": heading, "page": start_page,
                },
            ))
        buffer.clear()

    for line, page_number in lines:
        stripped = line.strip()
        para_match = _PARA.match(stripped)
        if para_match and (not para or int(para_match.group(1)) == int(para) + 1):
            flush()
            para, sub, clause, explanation = para_match.group(1), "", "", ""
            heading = stripped[:90]
            lead = ""
            start_page = page_number
            buffer.append(stripped)
            continue
        if not para:
            continue  # still in the preamble
        sub_match = _SUB.match(stripped)
        if sub_match and int(sub_match.group(1)) == (int(sub) + 1 if sub else 1):
            flush()
            sub, clause, explanation = sub_match.group(1), "", ""
            start_page = page_number
            buffer.append(stripped)
            continue
        clause_match = _CLAUSE.match(stripped)
        if clause_match:
            numeral = clause_match.group(1)
            expected = roman_order[roman_order.index(clause) + 1] if clause in roman_order else "i"
            if numeral == expected:
                flush()
                clause, explanation = numeral, ""
                start_page = page_number
                buffer.append(stripped)
                continue
        explanation_match = _EXPLANATION.match(stripped)
        if explanation_match and explanation_match.group(1):
            # Only numbered Explanations ("Explanation 1:") are cited on their own; an unnumbered
            # "Explanation:" explains the clause it sits in and stays inside that chunk.
            flush()
            explanation = explanation_match.group(1)
            start_page = page_number
            buffer.append(stripped)
            continue
        buffer.append(stripped)
    flush()
    logger.info("split into %d paragraph chunks", len(chunks))
    return chunks


def split_long_chunks(chunks: list[Document], chunk_size: int = 1500, chunk_overlap: int = 150) -> list[Document]:
    """RecursiveCharacterTextSplitter inside paragraphs longer than chunk_size; metadata is kept."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_documents(chunks)


def build_chunks(pdf_path: Path) -> list[Document]:
    return split_long_chunks(split_by_paragraph(load_pages(pdf_path)))


class RulesIndex(Protocol):
    name: str

    def search(self, query: str, k: int) -> list[Document]:
        """The k chunks closest to the query, best first."""


class FakeIndex:
    """For tests: ranks canned documents by how many query words they share."""

    name = "fake"

    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents
        self.queries: list[str] = []

    def search(self, query: str, k: int) -> list[Document]:
        self.queries.append(query)
        words = set(re.findall(r"[a-z]+", query.lower()))

        def overlap(document: Document) -> int:
            return len(words & set(re.findall(r"[a-z]+", document.page_content.lower())))

        ranked = sorted(self.documents, key=overlap, reverse=True)
        return [document for document in ranked[:k] if overlap(document) > 0]


class ChromaIndex:
    """Gemini embeddings + Chroma on disk. Built from the PDF on first use when data/chroma/ is empty."""

    def __init__(self, pdf_path: Path, persist_dir: Path, embedding_model: str) -> None:
        self.name = f"chroma:{pdf_path.name}:{embedding_model}"
        self.pdf_path = pdf_path
        self.persist_dir = persist_dir
        self.embedding_model = embedding_model
        self._store: Any = None
        self._cache: dict[tuple[str, int], list[Document]] = {}

    def _embeddings(self) -> Any:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=self.embedding_model)

    def _marker(self) -> Path:
        return self.persist_dir / "BUILD_OK.json"

    def _expected_marker(self, chunk_count: int) -> dict[str, Any]:
        return {"pdf": self.pdf_path.name, "embedding_model": self.embedding_model, "chunks": chunk_count}

    def _open(self) -> Any:
        if self._store is not None:
            return self._store
        from langchain_chroma import Chroma

        chunks = build_chunks(self.pdf_path)
        expected = self._expected_marker(len(chunks))
        marker = self._marker()
        complete = marker.exists() and json.loads(marker.read_text(encoding="utf-8")) == expected
        if not complete and self.persist_dir.exists():
            # A half-built index (rate limit, crash) or a different PDF/model: start again.
            logger.warning("Chroma folder %s is not a complete index for %s; rebuilding", self.persist_dir, self.pdf_path.name)
            shutil.rmtree(self.persist_dir)
        store = Chroma(collection_name=COLLECTION, embedding_function=self._embeddings(), persist_directory=str(self.persist_dir))
        if not complete:
            logger.info("building Chroma index with %d chunks in %s", len(chunks), self.persist_dir)
            add_in_batches(store, chunks)
            marker.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        self._store = store
        return store

    def search(self, query: str, k: int) -> list[Document]:
        """Same query, same answer: identical queries are embedded once per process."""
        key = (query, k)
        if key not in self._cache:
            self._cache[key] = with_backoff(lambda: self._open().similarity_search(query, k=k), what="query embedding")
        return list(self._cache[key])


# Pacing for the Gemini embedding API free tier: 100 requests per minute per model, where every
# text counts as one request. 25 texts every 16 seconds stays under it.
EMBED_BATCH = 25
EMBED_PAUSE_SECONDS = 16


def add_in_batches(store: Any, chunks: list[Document], batch_size: int = EMBED_BATCH, pause: float = EMBED_PAUSE_SECONDS) -> None:
    """Embed and add in small paced batches so the free-tier per-minute quota is not hit."""
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        with_backoff(lambda: store.add_documents(batch), what=f"embedding batch {start // batch_size}")
        logger.info("embedded %d/%d chunks", min(start + batch_size, len(chunks)), len(chunks))
        if start + batch_size < len(chunks):
            time.sleep(pause)


@lru_cache(maxsize=1)
def default_index() -> RulesIndex:
    settings = get_settings()
    return ChromaIndex(Path(settings.rbi_pdf_path), Path(settings.chroma_dir), settings.embedding_model)
