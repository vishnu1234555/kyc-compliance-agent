# Build progress (branch `claude/kyc-build`)

Started 30-10-2026 from `claude/kyc-evals`. One commit per roadmap item; every commit message
carries the scorecard before and after. A new session can start from the first item below that
is not `done`.

| Item | Status | Notes |
|---|---|---|
| 1 `extract` | done | regex reader + fine-tuned GLiNER2 model + ported validation/arbitration; extraction 269/279 regex only -> 276/279 with the model |
| 3 `retrieve_rules` | done | PyPDFLoader -> paragraph splitter (citation metadata) -> Gemini embeddings -> Chroma; findings.py turns errors/fields into queries; all eight POLICY paragraph numbers verified against the official text |
| 4 `decide` | pending | |
| 4b agentic `decide` | pending | |
| 5 `human_review` | pending | |
| 6 LangSmith | pending | |
| Docs | pending | README, docs/WALKTHROUGH.md, CLAUDE.md roadmap |

## Blocked in this session (not faked in code)

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
