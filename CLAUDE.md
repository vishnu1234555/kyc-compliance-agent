# KYC compliance agent (Indian NBFCs)

LangGraph agent that reads a KYC document, validates it against the Reserve Bank of India (Non-Banking Financial Companies – Know Your Customer) Directions, 2025, and emits approve / re_kyc / escalate with citations and an audit trail.

Rules source: RBI/DOR/2025-26/361, dated 28 Nov 2025, updated as on 29 Dec 2025, https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12943 (the 2016 RBI KYC Master Direction was repealed on 28 Nov 2025). Cite paragraphs as `para 42(1)`; the paragraphs we rely on, and the decision rules, are in `evals/POLICY.md`.

## Conventions
- Python 3.11, src layout. Setup: `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .`
- Test: `.venv/bin/python -m pytest -q`. Run once: `.venv/bin/python main.py`.
- `logging` everywhere, `print` only in `main.py` and for the scorecard lines of the `evals/` scripts. Type hints on every function.
- State lives only in `KYCState` (`src/kyc_agent/state.py`). Nodes are pure functions `KYCState -> KYCState`; add new fields to the TypedDict, never smuggle them through side channels.
- Every node appends one entry to `audit_log`: a `"node"` key plus what it did, e.g. validate writes `{"node": "validate", "errors": n}`. NBFC auditors need the trail.
- Secrets via `.env` (see `.env.example`), read through `config.get_settings()`. Keys are never printed, logged or written.
- A full Aadhaar number never leaves the process: `validate` masks it in the state right after checking it, the LLM sees masked fields only, LangSmith goes through the anonymizer in `tracing.py`, and the dataset upload is masked.
- Every graph invoke/stream takes `run_config(thread_id)` (checkpointer thread id + tracer). `DECIDE_MODE` is `rules`, `llm` or `agent`.
- LLM and agent eval answers are cached per case under `evals/llm_cache/<mode>/` (committed); a rerun skips cached cases, a daily-quota 429 stops the run cleanly and prints the resume command, and a mode's decision number is printed only once all 30 cases are cached ("partial: N/30" before that).
- Unit tests run offline: `tests/conftest.py` swaps in a null PII reader, a fake rules index, `DECIDE_MODE=rules` and a blank LangSmith key. Only `evals/` touches the real model, Gemini and LangSmith.
- Keep PRs small: one node per PR.
- RAG work uses LangChain components only (loaders, splitters, `langchain_chroma`). Never raw pypdf or hand-rolled retrieval.
- Every PR that changes behaviour adds or updates an eval under `evals/` and reports the before/after score. No change without a number.
- Vishnu must be able to explain every line. When a Claude session adds code, end with a short list of the new syntax it used, so it can be checked as the build goes.

## Roadmap (done in order, one commit each on `claude/kyc-build`; details and blockers in `docs/PROGRESS.md`)
0. DONE 30 Oct 2026. `evals/`: 30 synthetic KYC cases (`evals/cases.json`) with expected fields, validate errors, decision and citations, plus `evals/run_evals.py`. Rules and field formats in `evals/POLICY.md`. Baseline on the skeleton: decision 0/30, citation hit rate 0.0000, extraction 0/279, validate 30/30. Run it before and after every change.
1. DONE 30 Oct. `extract`: regex reader + Vishnu's fine-tuned GLiNER2 PII model (`VK1402/AADHAAR_Extractor`, threshold 0.30) merged by the validation/arbitration ported from Aadhaar-Final; Pydantic schema; aadhaar reported as printed, never masked by extract; missing fields left out. Extraction 269/279 regex only -> 276/279 with the model (the model adds 7 unlabelled names). Cases got `tags`; three Aadhaar check digits were fixed (POLICY.md section 8).
2. DONE 30 Oct. `validate`: deterministic checks (PAN, Aadhaar masked, PIN, DOB 18+); now also masks the Aadhaar number in the state right after recording the error.
3. DONE 30 Oct. `retrieve_rules`: `PyPDFLoader` -> paragraph-number splitter with citation metadata -> `RecursiveCharacterTextSplitter` -> Gemini embeddings -> `langchain_chroma` over `data/rbi_nbfc_kyc_directions_2025_from_html.pdf` (rendered from the official page text; see `data/SOURCE.md`). Findings (`findings.py`) drive the queries. All eight POLICY paragraph numbers verified. Citation hit rate 0.9412.
4. DONE 30 Oct. `decide`: `rules_decide` baseline (R1-R8 except R6) + Gemini Flash structured verdict on masked fields, errors and retrieved paragraphs; citations limited to retrieved paragraphs; approve skips `human_review`. The key's free tier (20 requests/day/model) cut the LLM run short: 25/30 with 7 rules fallbacks; rules alone 27/30.
4b. DONE 30 Oct (code; 30-case numbers blocked by the quota). Agentic `decide`: Gemini chooses `search_rbi_rules`, `check_kyc_due`, `compare_across_documents`, ends with `submit_verdict`; max 5 calls, every call in the audit; a verdict needs the date check and both comparisons first; approve carries no citations. Live on 3 cases. Eval flags: `DECIDE_MODE=agent`, `EVAL_GOLD_FIELDS=1`, `EVAL_CASES=`.
5. DONE 30 Oct. `human_review`: `interrupt()` + checkpointer (memory, or SQLite via `CHECKPOINT_DB`); the evals auto-resume as `eval-auto` and the scorecard says in how many cases.
6. DONE 30 Oct. LangSmith tracing when `LANGSMITH_API_KEY` is set, through the 12-digit anonymizer; cases uploaded (masked) as dataset `kyc-compliance-agent-cases`; experiment `kyc-rules-a3974733` run against it.

Next, when a billed Gemini key is available: the four 30-case decision numbers (rules, LLM without tools, LLM with tools, LLM with tools on gold fields), commands in `docs/PROGRESS.md`.
