# kyc-compliance-agent

A LangGraph agent for Indian NBFCs. It reads one existing customer's KYC file (plain text, as
OCR hands it over), extracts the fields, checks them, looks up the **Reserve Bank of India
(Non-Banking Financial Companies – Know Your Customer) Directions, 2025**, and ends with
`approve`, `re_kyc` or `escalate`, the RBI paragraphs it relied on, a one-line reason and an
audit trail. Anything but `approve` pauses for a human reviewer.

Everything here runs on synthetic documents. The decision policy is written down in
[`evals/POLICY.md`](evals/POLICY.md) (rules R1–R8, eight paragraphs of the Directions), every
decision names the rule that fired, and nothing in this repository is legal advice.

## The graph

```
START -> extract -> validate -> retrieve_rules -> decide --approve--> END
                                                         \--re_kyc / escalate--> human_review -> END
```

| Node | What it does | Mechanism |
|---|---|---|
| `extract` | Reads the record header and the documents on file into ten fields, plus each document's own fields | Two readers meet at a validator: regex/plain parsing for structured values (IDs, dates, PIN, header; OCR digit noise fixed first) and the fine-tuned GLiNER2 PII model ([VK1402/AADHAAR_Extractor](https://huggingface.co/VK1402/AADHAAR_Extractor)) for names and addresses and as a second reader for IDs. Candidates are merged by the validation + label-priority arbitration ported from [Aadhaar-Final](https://github.com/vishnu1234555/Aadhaar-Final) (Verhoeff check, masked Aadhaar valid, document-labelled values kept as printed). A Pydantic schema is the output contract. |
| `validate` | PAN format, Aadhaar masked, PIN format, DOB valid and 18+ | Deterministic checks; then masks any full Aadhaar number in the state (the error message keeps the record that it was visible) |
| `retrieve_rules` | Turns what is wrong into findings and fetches the RBI paragraphs for them | `findings.py` (exact date maths, cross-document comparison, validation errors); `PyPDFLoader` -> paragraph-number splitter with `para 42(1)`-style citation metadata -> `RecursiveCharacterTextSplitter` -> Gemini embeddings -> `langchain_chroma`; the top hit per query becomes a citation |
| `decide` | The verdict | Always a plain-code baseline (R1–R8 except R6). On top: `DECIDE_MODE=llm`, a Gemini Flash model with structured output on the masked fields, validation errors and retrieved paragraphs; or `DECIDE_MODE=agent`, the same model choosing tools (`search_rbi_rules`, `check_kyc_due`, `compare_across_documents`, at most 5 calls) and finishing with `submit_verdict`. Citations outside the retrieved paragraphs are dropped in code; an approve carries none |
| `human_review` | Pauses for a reviewer | LangGraph `interrupt()` with a checkpointer (memory, or SQLite via `CHECKPOINT_DB`); resumed with `Command(resume={...})`; the evals resume themselves as `eval-auto` and say so |

Tracing: with `LANGSMITH_API_KEY` set, every run is traced to LangSmith through an anonymizer
that rewrites 12-digit numbers to `XXXX-XXXX-<last four>` before they leave the process.

## How to run

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
cp .env.example .env        # GOOGLE_API_KEY (Gemini), LANGSMITH_API_KEY (optional)
.venv/bin/python -m pytest -q          # offline; fakes stand in for the model, Gemini and LangSmith
.venv/bin/python main.py               # one file through the graph, pause for review, resume
DECIDE_MODE=rules .venv/bin/python evals/run_evals.py    # the scorecard, no Gemini calls
```

The first run downloads the PII model (about 830 MB, CPU) and builds the Chroma index from
`data/` (about 5 minutes on the Gemini free tier, then kept in `data/chroma/`).

Knobs (all in `.env.example`): `DECIDE_MODE=rules|llm|agent`, `GEMINI_MODEL`,
`GEMINI_THINKING_BUDGET`, `PII_MODEL` (`off` = regex only), `PII_THRESHOLD`, `EMBEDDING_MODEL`,
`RBI_PDF_PATH`, `CHECKPOINT_DB`. Eval-only: `EVAL_CASES=case-01,case-15` for a subset,
`EVAL_GOLD_FIELDS=1` to decide on the expected fields, `LOG_LEVEL=INFO` for one line per case.
Other scripts: `evals/sweep_threshold.py` (PII threshold, chosen on the tune half of
`evals/split.json`), `evals/model_check.py` (one case with and without the model),
`evals/langsmith_dataset.py` (upload the masked cases as a LangSmith dataset and score against it).

## How to resume the evals

The Gemini key used so far is on the free tier: 20 calls per model per day. LLM and agent runs
are therefore resumable. Every answered case is cached under `evals/llm_cache/<mode>/` and a
rerun skips it; at the first daily-quota 429 the run stops, prints
`N of 30 done, resume tomorrow with: <command>` and exits 0. A mode's decision number is printed
only once all 30 cases are cached; before that the scorecard says `partial: N/30`. One model for
all LLM runs: `gemini-2.5-flash-lite`, temperature 0.

```bash
DECIDE_MODE=llm .venv/bin/python evals/run_evals.py
DECIDE_MODE=agent .venv/bin/python evals/run_evals.py
DECIDE_MODE=agent EVAL_GOLD_FIELDS=1 .venv/bin/python evals/run_evals.py
```

## Results

30 synthetic cases, labels as of 30-10-2026, runs on 30/31-10-2026. The PII threshold (0.30)
was chosen on the 15-case tune half of `evals/split.json`; the test half was never used for
choosing anything.

| What | Number | How it is measured |
|---|---|---|
| Decisions matching the label, `rules` mode | 27/30 | plain code applies POLICY.md R1–R8 except R6; the three misses are the R6 cases (a fictitious-looking name needs judgment) |
| Decisions matching the label, `llm` mode | partial: 23/30 answered, 23 cached | one structured-output Gemini call per case on masked fields, errors and retrieved paragraphs; resume to finish |
| Decisions matching the label, `agent` mode | partial: 0/30 answered, 0 cached | Gemini calls `check_kyc_due`, `compare_across_documents`, `search_rbi_rules`, then `submit_verdict`; resume to start |
| Decisions matching the label, `agent` on gold fields | partial: 0/30 answered, 0 cached | as agent, with the expected fields in place of the extracted ones |
| Extraction, held-out test half (15 cases) | 139/140 fields | threshold 0.30 chosen on the tune half; this number was not tuned on |
| Extraction, all 30 cases (tuned on itself) | 276/279 fields | the same threshold, measured on the cases it was chosen on |
| Extraction, regex reader alone | 269/279 fields | `PII_MODEL=off`; the model adds the 7 names printed without a label on Aadhaar cards and nothing else |
| Citation hit rate, `rules` mode | 0.8529 | mean over the 17 cases with expected citations of the share found in `rbi_citations`; the R6 cases contribute the misses |
| Validate on the extracted fields | 30/30 | `validate(extract output)` equals the expected error list |

What the model still misses: case 13 (`Nusrat Jahan`, no candidate at any threshold), case 22
(`Test Customer`, not treated as a person name) and the NPR letter's unlabelled address (case 06).
Agent mode was checked live on three cases on 30-10-2026 with `gemini-3.1-flash-lite`: case 15
`re_kyc` via `check_kyc_due`; case 24 `escalate` with `para 17(2)` after
`compare_across_documents("name")`; case 01 `approve` with no citations.

## Safety

- API keys come from environment variables only and are never logged or written.
- A full Aadhaar number never leaves the process: `validate` replaces it in the state with
  `XXXX-XXXX-<last four>` right after recording the error, both checkpointers serialize through
  a masking serializer (`checkpoints.py`) so no checkpoint in memory or on disk holds it, the
  LLM only ever sees masked fields, LangSmith traces pass through an anonymizer, and the dataset
  upload is masked (its four R3 labels are adjusted accordingly and the originals kept alongside).
- Every node writes one audit entry; agent tool calls, their arguments and results are in it.

## Limits

- The Gemini key is on the free tier (20 requests per model per day), so the LLM and agent
  rows above are partial until the resume commands have run on enough days or on a billed key.
- R6 (fictitious name) depends on the LLM, and on extraction having found the name at all.
- The PII model was trained on financial IDs: no date-of-birth or PIN label, an "Address" label
  that fires on name spans, Title Case names scored 0.34–0.98 (hence threshold 0.30).
- A resume from the checkpoint taken after `extract` (a crash before `validate`) gives validate
  the masked value, so the R3 error is not recorded on that resumed run.
- `data/` holds the Directions rendered from the official rbi.org.in page text, not the official
  PDF (the PDF host refused the build session); Annex I/II are not included. See `data/SOURCE.md`.
- The documents are synthetic and in a fixed shape; real OCR output will need more parsing
  rules. The eval is 30 cases and `validate` uses the machine clock (labels hold until 2028).
- The GLiNER2 checkpoint's tokenizer metadata is from transformers 5; `pii_model.load_gliner2`
  carries a small shim for transformers 4. `requirements.txt` pins the CPU torch wheel from the
  PyTorch index.

More: [`docs/LEARN.md`](docs/LEARN.md) (every file, what calls what, every piece of syntax),
[`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md) (one case through every node),
[`docs/PROGRESS.md`](docs/PROGRESS.md) (per-item notes, blockers, resume commands).
