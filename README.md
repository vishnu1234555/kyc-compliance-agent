# kyc-compliance-agent

A LangGraph agent for Indian NBFCs that reads one existing customer's KYC file (plain text, as
OCR would hand it over), extracts the fields, checks them, looks up the **Reserve Bank of India
(Non-Banking Financial Companies – Know Your Customer) Directions, 2025**, and ends with
`approve`, `re_kyc` or `escalate`, the RBI paragraphs it relied on, a one-line reason and an
audit trail. Anything but `approve` pauses for a human reviewer.

Everything here runs on synthetic documents. The decision policy it applies is written down in
[`evals/POLICY.md`](evals/POLICY.md) (rules R1–R8, eight paragraphs of the Directions), every
decision names the rule that fired, and nothing in this repository is legal advice.

## The graph

```
START -> extract -> validate -> retrieve_rules -> decide --approve--> END
                                                         \--re_kyc / escalate--> human_review -> END
```

| Node | What it does | How (the mechanism) |
|---|---|---|
| `extract` | Reads the record header and the documents on file into ten fields, plus the fields of each document | Two readers meet at a validator: regex/plain parsing for the structured values (IDs, dates, PIN, header; OCR digit noise fixed first) and Vishnu's fine-tuned GLiNER2 PII model ([VK1402/AADHAAR_Extractor](https://huggingface.co/VK1402/AADHAAR_Extractor)) for names and addresses and as a second reader for IDs. Candidates are merged by the validation + label-priority arbitration ported from [Aadhaar-Final](https://github.com/vishnu1234555/Aadhaar-Final) (Verhoeff check, masked Aadhaar valid, document-labelled values kept as printed). A Pydantic schema is the output contract. |
| `validate` | PAN format, Aadhaar masked, PIN format, DOB valid and 18+ | Deterministic checks; then masks any full Aadhaar number in the state (the error message keeps the record that it was visible) |
| `retrieve_rules` | Turns what is wrong into findings and fetches the RBI paragraphs for them | `findings.py` (exact date maths, cross-document comparison, validation errors); `PyPDFLoader` -> split by paragraph number with `para 42(1)`-style citation metadata -> `RecursiveCharacterTextSplitter` -> Gemini embeddings -> `langchain_chroma`; the top hit per query becomes a citation |
| `decide` | The verdict | Always a plain-code baseline (R1–R8 except R6). On top: `DECIDE_MODE=llm`, a Gemini Flash model with structured output on the masked fields, validation errors and retrieved paragraphs; or `DECIDE_MODE=agent`, the same model choosing tools (`search_rbi_rules`, `check_kyc_due`, `compare_across_documents`, max 5 calls) and finishing with `submit_verdict`. Citations outside the retrieved paragraphs are dropped in code; an approve carries none |
| `human_review` | Pauses for a reviewer | LangGraph `interrupt()` with a checkpointer (memory, or SQLite via `CHECKPOINT_DB`); resumed with `Command(resume={...})`; the evals resume themselves as `eval-auto` and say so |

Tracing: with `LANGSMITH_API_KEY` set, every run is traced to LangSmith through an anonymizer
that rewrites 12-digit numbers to `XXXX-XXXX-<last four>` before they leave the process.

## How to run

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
cp .env.example .env        # GOOGLE_API_KEY (Gemini), LANGSMITH_API_KEY (optional)
.venv/bin/python -m pytest -q          # 308 tests, offline, fakes for the model and the LLM
.venv/bin/python main.py               # one file through the graph, pause for review, resume
.venv/bin/python evals/run_evals.py    # the scorecard over evals/cases.json
```

The first run downloads the PII model (about 830 MB, CPU) and builds the Chroma index from
`data/` (about 5 minutes on the Gemini free tier, then cached in `data/chroma/`).

Knobs (all in `.env.example`): `DECIDE_MODE=rules|llm|agent`, `GEMINI_MODEL`,
`GEMINI_THINKING_BUDGET`, `PII_MODEL` (`off` = regex only), `PII_THRESHOLD`, `EMBEDDING_MODEL`,
`RBI_PDF_PATH`, `CHECKPOINT_DB`. Eval-only: `EVAL_CASES=case-01,case-15` for a subset,
`EVAL_GOLD_FIELDS=1` to decide on the expected fields, `LOG_LEVEL=INFO` for one line per case.
`evals/langsmith_dataset.py` uploads the cases as a LangSmith dataset (masked) and runs the
scorecard against it; `evals/sweep_threshold.py` sweeps the PII-model threshold on the tune half.

LLM and agent runs are resumable: every answered case is cached under `evals/llm_cache/<mode>/`
and a rerun skips it; when the Gemini daily quota is hit the run stops, prints
"N of 30 done, resume tomorrow with: <command>" and exits 0. A mode's decision accuracy is only
printed once all 30 cases are cached; before that the scorecard says "partial: N/30".

## Scorecard

30 synthetic cases, labels as of 30-10-2026, run on 30-10-2026. "Decision" is the decide node's
accuracy in the mode named; "rules" is the plain-code baseline that every run also reports.

| After | Decision | Rules only | Citation hit rate | Extraction, regex + model | Regex only | Validate on extracted |
|---|---|---|---|---|---|---|
| item 0 (skeleton) | 0/30 | – | 0.0000 | 0/279 | – | – |
| item 1 `extract` | 0/30 | – | 0.0000 | 276/279 | 269/279 | 30/30 |
| item 3 `retrieve_rules` | 0/30 | – | 0.9412 | 276/279 | 269/279 | 30/30 |
| item 4 `decide`, `llm` (gemini-2.5-flash-lite; 7 cases fell back to the rules when the daily quota ran out) | 25/30 | 27/30 | 0.7157 | 276/279 | 269/279 | 30/30 |
| items 4b, 5, 6 (`rules` mode, no LLM call) | 27/30 | 27/30 | 0.8529 | 276/279 | 269/279 | 30/30 |

What the PII model adds over regex alone, per field (hits/total, 30 cases; 17 tagged
`unlabelled` print at least one value without a label):

| Field | Regex + model | Regex only | Model adds |
|---|---|---|---|
| name | 28/30 (unlabelled 15/17) | 21/30 (unlabelled 8/17) | +7, all names on Aadhaar cards printed without a "Name:" label |
| address | 29/30 | 29/30 | 0 (its "Address" label never fires on an address) |
| dob, pan, form60, aadhaar, pincode, doc_type, risk_category, last_kyc_date | all right | all right | 0 |

The PII-model threshold is chosen on a fixed tune half of 15 cases (`evals/split.json`, balanced
on the `unlabelled` tag) and reported on the other 15: threshold 0.30, test half 139/140 =
0.9929. The full-30 number above (276/279) is the same threshold tuned on itself. Run
`evals/sweep_threshold.py` to reproduce; `evals/model_check.py` shows one case with and without
the model, and the scorecard header names the Hugging Face revision that was loaded.

Still missed with the model: case 13 (`Nusrat Jahan`: no candidate at any threshold), case 22
(`Test Customer`: not considered a person name), the NPR letter's unlabelled address (case 06).

The rules baseline misses exactly the three R6 cases (22, 23, 29): a fictitious-looking name
needs judgment, so the baseline never fires R6.

The four decision numbers, all LLM runs on `gemini-2.5-flash-lite` at temperature 0 (as of
31-10-2026; the key allows 20 Gemini calls per day, so the LLM modes fill up over several days
through the per-case cache in `evals/llm_cache/`; a mode's number is printed only once all 30
cases are cached):

| Mode | Decision accuracy | Not answered yet | How the decision is made |
|---|---|---|---|
| rules | 27/30 = 0.9000 | - | plain code applies POLICY.md R1-R8 except R6 |
| llm, no tools | partial: 23/30 | 7 | one structured-output call on masked fields, errors and retrieved paragraphs |
| agent, tools | partial: 0/30 | 30 | the model calls check_kyc_due, compare_across_documents, search_rbi_rules, then submit_verdict |
| agent on gold fields | partial: 0/30 | 30 | as agent, with the expected fields in place of the extracted ones |

Resume commands are in [`docs/PROGRESS.md`](docs/PROGRESS.md). Agent mode was checked live on
three cases with `gemini-3.1-flash-lite` on 30-10-2026: case 15 `re_kyc` via `check_kyc_due`;
case 24 `escalate` with `para 17(2)` after `compare_across_documents("name")`; case 01
`approve` with no citations.

## Safety

- API keys come from environment variables only and are never logged or written.
- A full Aadhaar number never leaves the machine: `validate` replaces it in the state with
  `XXXX-XXXX-<last four>` right after recording the error, the LLM only ever sees masked fields,
  LangSmith traces pass through an anonymizer, and the dataset upload is masked (its four
  R3 labels are adjusted accordingly and the originals kept alongside).
- Every node writes one audit entry; agent tool calls, their arguments and results are in it.

## Known limits

- The Gemini key used to build this is on the free tier (20 requests per model per day), so
  the LLM rows above are partial and the agent was checked live on three cases only.
- R6 (fictitious name) depends on the LLM, and on extraction having found the name at all.
- The PII model was trained on financial IDs: no date-of-birth or PIN label, an "Address"
  label that fires on name spans, Title Case names scored 0.34–0.98 (hence threshold 0.30).
- With a persistent checkpointer, the snapshot after `extract` holds the Aadhaar number as
  printed; `validate` masks it one step later. Keep the in-memory checkpointer or encrypt the
  SQLite file before real data.
- `data/` holds the Directions rendered from the official rbi.org.in page text, not the official
  PDF (the PDF host refused the build session); Annex I/II are not included. See `data/SOURCE.md`.
- The documents are synthetic and in a fixed shape; real OCR output will need more parsing
  rules. The eval is 30 cases and `validate` uses the machine clock (labels hold until 2028).
- The GLiNER2 checkpoint's tokenizer metadata is from transformers 5; `pii_model.load_gliner2`
  carries a small shim for transformers 4. `requirements.txt` pins the CPU torch wheel from the
  PyTorch index.
