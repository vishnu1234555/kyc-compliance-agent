# KYC compliance agent (Indian NBFCs)

LangGraph agent that reads a KYC document, validates it against the Reserve Bank of India (Non-Banking Financial Companies – Know Your Customer) Directions, 2025, and emits approve / re_kyc / escalate with citations and an audit trail.

Rules source: RBI/DOR/2025-26/361, dated 28 Nov 2025, updated as on 29 Dec 2025, https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12943 (the 2016 RBI KYC Master Direction was repealed on 28 Nov 2025). Cite paragraphs as `para 42(1)`; the paragraphs we rely on, and the decision rules, are in `evals/POLICY.md`.

## Conventions
- Python 3.11, src layout. Setup: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .`
- Test: `.venv/bin/python -m pytest -q`. Run once: `.venv/bin/python main.py`.
- `logging` everywhere, `print` only in `main.py` and for the scorecard lines of `evals/run_evals.py`. Type hints on every function.
- State lives only in `KYCState` (`src/kyc_agent/state.py`). Nodes are pure functions `KYCState -> KYCState`; add new fields to the TypedDict, never smuggle them through side channels.
- Every node appends one entry to `audit_log`: a `"node"` key plus what it did, e.g. validate writes `{"node": "validate", "errors": n}`. NBFC auditors need the trail.
- Secrets via `.env` (see `.env.example`), read through `config.get_settings()`.
- Keep PRs small: one node per PR.
- RAG work uses LangChain components only (loaders, splitters, `langchain_chroma`). Never raw pypdf or hand-rolled retrieval.
- Every PR that changes behaviour adds or updates an eval under `evals/` and reports the before/after score. No change without a number.
- Vishnu must be able to explain every line. When a Claude session adds code, end with a short list of the new syntax it used, so it can be checked as the build goes.

## Roadmap (do in order, one PR each)
0. DONE 30 Oct 2026. `evals/`: 30 synthetic KYC cases (`evals/cases.json`) with expected fields, validate errors, decision and citations, plus `evals/run_evals.py`, which prints decision accuracy, citation hit rate, extraction accuracy and validate accuracy. Rules and field formats in `evals/POLICY.md`. Baseline on the skeleton: decision 0/30 = 0.0000, citation hit rate 0.0000 (17 cases with citations), extraction 0/279 = 0.0000, validate 30/30 = 1.0000. Run it before and after every later PR.
1. `extract`: Vishnu's own fine-tuned GLiNER2 PII model, called as a tool -> `extracted_fields` (name, dob, pan, form60, aadhaar, address, pincode, doc_type, plus risk_category and last_kyc_date from the record header). Pydantic schema, no free-form JSON parsing. Formats are in `evals/POLICY.md` section 4: aadhaar is reported as printed (`XXXX-XXXX-1234`, or `9999-1234-5678` when the document shows it in full; extract must never mask it itself, validate checks that). extract must leave a field out when the file doesn't have it, because validate treats None as a wrong value.
2. DONE 30 Oct. `validate`: deterministic checks only, exactly what `validators.py` does: PAN format, Aadhaar masked as XXXX-XXXX-1234, PIN code format, DOB valid and 18+. Populate `validation_errors`. No LLM here.
3. `retrieve_rules`: LangChain `PyPDFLoader` + `RecursiveCharacterTextSplitter` on the NBFC KYC Directions, 2025 PDF into `langchain_chroma`, retrieve by `validation_errors`, fill `rbi_citations` with paragraph numbers in the `evals/POLICY.md` format (e.g. `para 42(1)`).
4. `decide`: LLM given fields + errors + citations -> `decision`. Add conditional edge: `approve` skips `human_review`.
5. `human_review`: LangGraph `interrupt()` + checkpointer so a reviewer can resume.
6. LangSmith tracing on by default when LANGSMITH_API_KEY is set; wire the `evals/` scorecard to a LangSmith dataset.
