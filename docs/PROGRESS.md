# Build progress (branch `claude/kyc-build`)

Started 30-10-2026 from `claude/kyc-evals`. One commit per roadmap item; every commit message
carries the scorecard before and after. A new session can start from the first item below that
is not `done`.

| Item | Status | Notes |
|---|---|---|
| 1 `extract` | done | regex reader + fine-tuned GLiNER2 model + ported validation/arbitration; extraction 269/279 regex only -> 276/279 with the model |
| 3 `retrieve_rules` | pending | |
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
