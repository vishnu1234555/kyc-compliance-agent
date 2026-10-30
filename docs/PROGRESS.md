# Build progress (branch `claude/kyc-build`)

Started 30-10-2026 from `claude/kyc-evals`. One commit per roadmap item; every commit message
carries the scorecard before and after. A new session can start from the first item below that
is not `done`.

| Item | Status | Notes |
|---|---|---|
| 1 `extract` | done | regex reader + fine-tuned GLiNER2 model + ported validation/arbitration; extraction 269/279 regex only -> 276/279 with the model |
| 3 `retrieve_rules` | done | PyPDFLoader -> paragraph splitter (citation metadata) -> Gemini embeddings -> Chroma; findings.py turns errors/fields into queries; all eight POLICY paragraph numbers verified against the official text |
| 4 `decide` | done | rules_decide baseline (R1-R8 except R6) + Gemini Flash-Lite structured verdict, citations filtered to retrieved paragraphs, approve skips human_review; LLM evals capped by the key's free tier (20 requests/day/model) |
| 4b agentic `decide` | done (code + 3-case live check); 30-case numbers blocked by quota | tool loop: search_rbi_rules, check_kyc_due, compare_across_documents, submit_verdict; max 5 investigation calls; every call in the audit entry |
| 5 `human_review` | pending | |
| 6 LangSmith | pending | |
| Docs | pending | README, docs/WALKTHROUGH.md, CLAUDE.md roadmap |

## Blocked in this session (not faked in code)

- **Gemini free tier on this GOOGLE_API_KEY: 20 generateContent requests per day per model.**
  Found while running item 4's eval: `gemini-2.5-flash` hit `GenerateRequestsPerDayPerProjectPerModel-FreeTier,
  limit: 20` after the first few cases, and `gemini-2.5-flash-lite` the same after about 22. A
  30-case eval needs 30 calls (item 4) or roughly 100 (item 4b, 2-4 calls per case), so no
  single model can finish a run on this key in one day. What was done: the decide node falls
  back to the plain-code rules verdict when the LLM is out of quota and the scorecard prints how
  many cases that happened to ("N LLM errors fell back to the rules"); a spent daily quota is
  not retried. The committed item 4 scorecard is therefore a mix (see that commit). To rerun with
  a billed key: `GEMINI_MODEL=gemini-2.5-flash .venv/bin/python evals/run_evals.py`.

- **CPU-only torch wheels.** `download.pytorch.org/whl/cpu` answers, but the wheel files
  redirect to `download-r2.pytorch.org`, which the session's network policy rejects (403 on
  CONNECT). `requirements.txt` keeps the reference pin `torch==2.5.1+cpu` from the PyTorch
  index; this session installed `torch==2.5.1` from PyPI instead (same version, CUDA build,
  runs on CPU). On a normal machine `pip install -r requirements.txt` works as written.
- **The RBI PDF host.** `rbidocs.rbi.org.in` closes the connection to this session's HTTP
  client with no response (the CONNECT succeeds, then an empty reply); the master-direction
  page itself on `rbi.org.in` is reachable. See item 3 for what was done instead.

## Judgment calls so far

- See `evals/POLICY.md` section 8 for the eval-side calls (three Aadhaar check digits, tags).

## Item 1: extract (done)

What was built (`src/kyc_agent/`): `ocr.py` (O->0, I/l->1 inside digit runs), `documents.py`
(header, document blocks, doc_type/form60 keyword rules), `regex_reader.py` (labelled lines,
ID sweeps, bare lines on PAN cards), `pii_model.py` (GLiNER2 wrapper, fakes for tests),
`arbitration.py` (port of the PII project's validation.py), `extraction.py` (merge, first
document that shows a field, Pydantic schema in `schema.py`), `nodes.extract`, and masking in
`nodes.validate`. Evals: `tags` per case, per-field table, regex-only column, validate on
extracted fields, `evals/sweep_threshold.py`.

Findings about the model on this eval set (threshold sweep, 279 fields):

| threshold | 0.20 | 0.30 | 0.40 | 0.50 | 0.70 | 0.90 | 0.99 |
|---|---|---|---|---|---|---|---|
| fields right | 275 | 275 | 274 | 274 | 274 | 274 | 272 |

- Default threshold set to 0.30 (`PII_THRESHOLD`). Vishnu's 0.99 came from a sweep over the ID
  labels; here the Title Case names on Aadhaar cards score 0.34-0.98, and at 0.99 four of them
  drop out.
- What the model adds over regex: 7 names, all on unlabelled Aadhaar cards (name 21/30 ->
  28/30; unlabelled split 8/17 -> 15/17). Nothing on any other field: regex already reads
  every PAN, Aadhaar, PIN, date and header value, and the model's "Address" label never fires
  on an address (it fires on name spans, with confidence up to 0.98, which is why Address has
  the lowest arbitration priority).
- Still missed with the model: case 13 (`Nusrat Jahan`, the model proposes nothing even at
  0.2), case 22 (`Test Customer`, the model does not consider it a person name), case 06's
  address (the NPR letter prints it under "at the address below", no label; the model has no
  usable Address label).
- The model often runs a span into the next line (`Lakshmi Narayanan\nDOB`), so model spans
  are cut at the first line break before arbitration.
- The checkpoint's tokenizer_config.json was written by transformers 5 (`tokenizer_class:
  TokenizersBackend`, list-valued `extra_special_tokens`); gliner2 2.0.0 pins transformers 4,
  which cannot load it. `pii_model.load_gliner2` falls back to loading tokenizer.json as the
  DeBERTa-v3 fast tokenizer. Worth knowing for the Aadhaar-Final backend too, which uses the
  same pins.
- Load time about 5 s on CPU, about 0.2-0.4 s per document block.

Changes to the arbitration port, both documented in the module docstring: a masked Aadhaar is
valid; a candidate the document labels itself (`trusted`) skips the structure check and wins
any overlap against a guessed candidate.

## Item 3: retrieve_rules (done)

- `data/rbi_nbfc_kyc_directions_2025_from_html.pdf` + `data/SOURCE.md`: the official PDF host
  refused the session (see "Blocked"), so the PDF was rendered with LibreOffice from the full
  text of the Directions on the rbi.org.in master-direction page (paragraphs 1-76, same
  numbering and wording; Annex I/II not included). `RBI_PDF_PATH` points at it; drop the
  official PDF in and delete `data/chroma/` to switch.
- The eight paragraph numbers in POLICY.md were checked against that text: all correct, no
  label changed (POLICY.md section 1 says so now).
- `src/kyc_agent/rules_index.py`: `split_by_paragraph` cuts at "N.", "(n)", "(roman)" and
  numbered "Explanation N:" lines, with the numbers required to increase by one so a stray
  number inside a sentence does not open a new paragraph; every chunk carries `citation`
  ("para 42(1)", "para 5(1)(xiv)", "para 23 Explanation 1"). Sub-chunks start with their
  paragraph's lead sentence (without it, the one-line "(5) the PAN or ... Form No. 60" lost to
  paragraph 70 "Quoting of PAN"). 430 paragraph chunks, 436 after
  `RecursiveCharacterTextSplitter` (1500/150) on the long ones.
- `src/kyc_agent/findings.py` + `rules.py`: what retrieval is driven by. R1 from doc_type, R2
  from the PAN error or missing PAN/Form 60, R3/R5/R8 from validate's errors, R4 from exact
  date maths (`rules.kyc_due`, 29 Feb clamps to 28 Feb), R7 from per-document name/dob
  comparison and a refusal phrase in a branch note, R6 only as a *tentative* hint (placeholder
  words such as TEST, DEMO, XYZ) that fetches para 17(1) for the LLM and never fires a rule.
  `extract` now keeps a branch note's text in its document entry for this.
- `retrieve_rules`: one query per finding (R1 has two), top-`RETRIEVAL_K`=3 chunks kept in
  `retrieved_rules` for the LLM, the top-1 citation per query goes to `rbi_citations`, in rule
  order, deduplicated.
- Gemini embeddings on this key are on the **free tier**: 100 embed requests per minute per
  model, each text counting as one. The build is paced (25 chunks per batch, 16 s pause, backoff
  5/15/30/60 s on 429) and takes about 5 minutes; a `BUILD_OK.json` marker makes a half-built
  index rebuild from scratch. Identical queries are embedded once per process. The first build
  used `gemini-embedding-2`; the index was rebuilt on `gemini-embedding-001` (same 3072 dims)
  after the chunk-lead change, to spend a fresh per-model quota bucket, and that is now the
  default `EMBEDDING_MODEL`.
- `langchain_community.document_loaders.PyPDFLoader` prints a deprecation warning
  (langchain-community is being sunset); kept because the roadmap names PyPDFLoader.

## Item 4: decide (done)

- `decide.py`: `rules_decide` (POLICY R1-R8 except R6, from the findings; the baseline and the
  fallback), `Verdict` (Pydantic: decision, citations, one-line reason), `build_messages`
  (as-of date, masked fields, validation errors, retrieved paragraphs; never the raw document),
  `llm_decide` (structured output, temperature 0, backoff on 429/503), and
  `keep_retrieved_citations` (a citation the LLM makes that was not retrieved is dropped in code).
- `nodes.decide`: `DECIDE_MODE=llm` (default) or `rules`; the rules verdict is always computed
  and stored in `rules_verdict`, so one eval run reports both. `graph.py`: conditional edge,
  approve -> END, otherwise human_review.
- Model: `GEMINI_MODEL=gemini-2.5-flash-lite` by default, `GEMINI_THINKING_BUDGET=0`.
  gemini-3.8-flash answered a one-word prompt in 70 s with 77 reasoning tokens and returned 503
  "high demand" twice during the session; gemini-2.5-flash answered in about 1 s but its free
  daily quota (20) was gone after the first probes and cases; Flash-Lite has the same cap. The
  model is one env var away.
- The eval scorecard now prints the decide-node accuracy and the rules-only accuracy side by
  side, and names the model.
- Item 4 scorecard: decide node 25/30, rules only 27/30, citation hit rate 0.7157. Seven cases
  (23, 25-30) fell back to the rules when Flash-Lite's daily quota ran out; of the 23
  LLM-decided cases 20 were right. LLM misses: case 08 (adult born 12-12-2006, no validation
  error, escalated as a minor: age maths the model did on its own; item 4b hands dates to a
  tool), case 22 (its placeholder name was never extracted, so R6 had nothing to judge), case
  24 (a name mismatch across documents, invisible without the documents view; item 4b adds the
  compare tool). Flash-Lite also tends to cite one of two paragraphs for a rule (cases 10, 19,
  20), which is what pulled the citation hit rate down from 0.94.

## Item 4b: agentic decide (code done; full scorecards blocked by the free-tier quota)

- `agent.py`: Gemini with `bind_tools(..., tool_choice="any")` chooses among
  `search_rbi_rules(query)` (the item 3 index; whatever it returns becomes citable),
  `check_kyc_due(risk_category, last_kyc_date)` (exact date maths, `rules.kyc_due`),
  `compare_across_documents(field)` (every document's value for name/dob/...), and ends with
  `submit_verdict(decision, citations, reason)`, whose arguments are the structured output.
  At most 5 investigation calls per case; after that only submit_verdict is allowed. Every
  call, its arguments and its result (first 400 chars) go into the decide audit entry
  (`tool_calls`), with `llm_calls` and `budget_exhausted`.
- Two deterministic guardrails, both visible in the audit: a verdict is not recorded until
  check_kyc_due and compare_across_documents("name"/"dob") have been called (the model is sent
  back once; in the first live run Flash-Lite skipped the comparison on case 24 and approved
  a name mismatch), and an approve carries no citations (the model cited para 42(1) on an
  approve). Citations are still filtered to retrieved paragraphs in code.
- `DECIDE_MODE=agent` selects it; `EVAL_CASES=case-01,case-15` runs a subset;
  `EVAL_GOLD_FIELDS=1` makes validate/retrieve_rules/decide see the expected fields instead of
  the extracted ones (extract still runs for its per-document values and its score).
- Live check on 3 cases with `gemini-3.1-flash-lite` (the only bucket with quota left):
  case 15 re_kyc via check_kyc_due (2 LLM calls, 18.7 s), case 24 approve before the guardrail
  (the model never compared documents), case 01 approve with a stray para 42(1) citation
  (now dropped). See the commit for the rerun after the guardrails.
- **Blocked:** the four 30-case numbers the roadmap asks for (rules baseline, LLM without
  tools, LLM with tools, LLM with tools on gold fields) need about 30 + 30 + 90 + 90
  generateContent calls; this key allows 20 per model per day. The rules baseline is in every
  scorecard (27/30). Commands for a billed key or another day:
  `DECIDE_MODE=llm .venv/bin/python evals/run_evals.py`,
  `DECIDE_MODE=agent .venv/bin/python evals/run_evals.py`,
  `DECIDE_MODE=agent EVAL_GOLD_FIELDS=1 .venv/bin/python evals/run_evals.py`.
