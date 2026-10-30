# KYC compliance agent (Indian NBFCs)

LangGraph agent that reads a KYC document, validates it against RBI KYC Master Direction rules, and emits approve / re_kyc / escalate with citations and an audit trail.

## Conventions
- Python 3.11, src layout. Setup: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .`
- Test: `.venv/bin/python -m pytest -q`. Run once: `.venv/bin/python main.py`.
- `logging` everywhere, `print` only in `main.py`. Type hints on every function.
- State lives only in `KYCState` (`src/kyc_agent/state.py`). Nodes are pure functions `KYCState -> KYCState`; add new fields to the TypedDict, never smuggle them through side channels.
- Every node appends one entry to `audit_log`: a `"node"` key plus what it did, e.g. validate writes `{"node": "validate", "errors": n}`. NBFC auditors need the trail.
- Secrets via `.env` (see `.env.example`), read through `config.get_settings()`.
- Keep PRs small: one node per PR.
- RAG work uses LangChain components only (loaders, splitters, `langchain_chroma`). Never raw pypdf or hand-rolled retrieval.
- Every PR that changes behaviour adds or updates an eval under `evals/` and reports the before/after score. No change without a number.
- Vishnu must be able to explain every line. When a Claude session adds code, end with a short list of the new syntax it used, so it can be checked as the build goes.

## Roadmap (do in order, one PR each)
0. `evals/` first: 20 synthetic KYC docs with expected decision and citations, plus a script that prints decision accuracy and citation hit rate. Run it before and after every later PR.
1. `extract`: Gemini (GOOGLE_API_KEY) structured-output call -> `extracted_fields` (name, dob, pan, aadhaar (masked XXXX-XXXX-1234), address, pincode, doc_type). Pydantic schema, no free-form JSON parsing.
2. DONE 30 Oct. `validate`: deterministic checks only (PAN regex, DOB sanity, address present, OVD type allowed). Populate `validation_errors`. No LLM here.
3. `retrieve_rules`: LangChain `PyPDFLoader` + `RecursiveCharacterTextSplitter` on the RBI KYC Master Direction PDF into `langchain_chroma`, retrieve by `validation_errors`, fill `rbi_citations` with section numbers.
4. `decide`: LLM given fields + errors + citations -> `decision`. Add conditional edge: `approve` skips `human_review`.
5. `human_review`: LangGraph `interrupt()` + checkpointer so a reviewer can resume.
6. LangSmith tracing on by default when LANGSMITH_API_KEY is set; wire the `evals/` scorecard to a LangSmith dataset.
