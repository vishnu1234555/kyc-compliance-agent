# Walkthrough: one KYC file through the graph

This is the study guide. Each node gets three things: what it does in plain English, the state
going in and coming out for one real case, and every piece of syntax that node introduced, one
line each. The case is `case-24` from `evals/cases.json`: a passport that says ANJALI MEHRA and
a PAN card that says ANJALI MALHOTRA. Expected outcome: `escalate`, citing `para 17(2)`.

States below are from a `DECIDE_MODE=rules` run (no LLM call), trimmed to what changed.

## 0. The shape of the project

- **State** is one `TypedDict`, `KYCState` in `src/kyc_agent/state.py`. A TypedDict is a plain
  dict with a declared list of keys and types; nothing enforces it at runtime, it is the contract.
- **Nodes** are plain functions `KYCState -> KYCState` in `src/kyc_agent/nodes.py`. Each returns
  a *new* dict (`{**state, "key": value}` copies the old dict and overrides keys) with one more
  entry in `audit_log` (`[*state["audit_log"], entry]` copies the list and appends).
- **The graph** is built in `src/kyc_agent/graph.py`: `StateGraph(KYCState)`, `add_node(name,
  function)`, `add_edge(a, b)`, one `add_conditional_edges`, and `compile(checkpointer=...)`.
- Every run needs a config: `run_config(thread_id)` returns `{"configurable": {"thread_id": ...}}`
  (the checkpointer files the run under that id) plus the LangSmith tracer when a key is set.

Input state at START (everything else empty):

```json
{"raw_document": "KYC RECORD - PERIODIC REVIEW\nCustomer ID: NB-10024\nRisk category: High\nLast KYC date: 01-04-2026\n\n[Document 1: Passport]\nREPUBLIC OF INDIA\nPassport No: U3300871\nName: ANJALI MEHRA\nDate of birth: 12/02/1987\nPlace of birth: GURUGRAM\nAddress: 9 Sector 14, Gurugram, Haryana\nPIN: 122001\n\n[Document 2: PAN card]\nINCOME TAX DEPARTMENT\nPermanent Account Number Card\nVXKPM3321S\nANJALI MALHOTRA\n12/02/1987\n"}
```

## 1. extract

**What it does.** Fixes OCR look-alikes inside numbers (O->0, I/l->1), splits the file into the
record header and one block per document, classifies each block by keywords (passport, PAN
card, Form 60, branch note, ...), then runs two readers over it. The regex reader
(`regex_reader.py`) takes labelled lines (`Name:`, `PIN Code:`), ID numbers it can recognise by
shape (PAN, masked and full Aadhaar) and the bare lines cards print without a label (the PAN on
a PAN card, a date on its own line, a six-digit PIN under an address). The PII model
(`pii_model.py`, GLiNER2 fine-tune) is run on each block for names and addresses and as a second
opinion on IDs. All candidates go through `arbitration.py` (ported from Aadhaar-Final): a
candidate that fails its label's structure check is dropped (12-digit runs need a valid
Verhoeff check digit; a masked number is valid), overlapping candidates are resolved by what the
document labels itself first, then by label specificity. Each document keeps its own fields;
the file's fields come from the first document that shows each one. `schema.py` (Pydantic)
checks the result; missing fields are left out, never sent as null.

**Output for case 24** (new keys only):

```json
"extracted_fields": {"name": "ANJALI MEHRA", "dob": "12-02-1987", "pan": "VXKPM3321S", "form60": false,
                     "address": "9 Sector 14, Gurugram, Haryana", "pincode": "122001", "doc_type": "passport",
                     "risk_category": "high", "last_kyc_date": "01-04-2026"},
"documents": [{"kind": "passport", "title": "Document 1: Passport", "name": "ANJALI MEHRA", "dob": "12-02-1987",
               "address": "9 Sector 14, Gurugram, Haryana", "pincode": "122001"},
              {"kind": "pan_card", "title": "Document 2: PAN card", "name": "ANJALI MALHOTRA", "dob": "12-02-1987",
               "pan": "VXKPM3321S"}],
"audit_log": [{"node": "extract", "reader": "VK1402/AADHAAR_Extractor", "customer_id": "NB-10024",
               "documents": ["passport", "pan_card"], "fields": ["address", "dob", "doc_type", "form60",
               "last_kyc_date", "name", "pan", "pincode", "risk_category"],
               "candidates": {"regex_candidates": 6, "model_candidates": 9, "kept": 8, "rejected": 7}}]
```

The PAN card's name `ANJALI MALHOTRA` is a bare line, so only the model could read it; that is
what makes the mismatch visible later. No Aadhaar on file, so no `aadhaar` key at all.

**Syntax introduced** (one line each):

- `str.maketrans({...})` + `text.translate(table)`: a character-for-character replacement table (`ocr.py`).
- `re.sub(pattern, function, text)`: the function receives each match and returns its replacement.
- `re.compile(r"...", re.I | re.M)`: a compiled pattern; `re.I` ignores case, `re.M` makes `^`/`$` match at every line.
- `(?P<value>...)` and `match.group("value")`, `match.start("value")`: a named group and its text / offset.
- `@dataclass(frozen=True)`: a small immutable record class with an auto-generated constructor (`documents.Block`).
- `@property`: a method you read like an attribute (`block.body`).
- `class Candidate(TypedDict, total=False)`: a dict shape where keys may be missing.
- `Protocol`: an interface by shape; anything with a `read(text)` method is a `PIIReader`, no inheritance needed.
- `@lru_cache(maxsize=1)` on `default_reader()`: the function body runs once, later calls return the same object.
- `from gliner2 import GLiNER2` inside a function: a lazy import, so unit tests never load torch.
- `sorted(items, key=lambda c: (c["start"], c["label"]))`: sort by a tuple, first element first.
- `key=_rank` returning a tuple `(tier, -confidence, start)`: negative numbers sort "biggest first".
- `BaseModel` with `Field(default=None, pattern=r"^\d{2}-\d{2}-\d{4}$")` and `Literal[...]`: Pydantic validates shape and allowed values.
- `model_config = ConfigDict(extra="forbid")`: unknown keys are an error, not ignored.
- `model.model_dump(exclude_none=True)`: the Pydantic object back to a dict without the None fields.
- `next((x for x in items if cond), default)`: the first matching item or a default.
- `{**a, "k": v}` / `[*items, extra]`: copy-and-extend a dict / a list.

## 2. validate

**What it does.** Runs four deterministic checks on whichever of `pan`, `aadhaar`, `pincode`,
`dob` are present (a missing field is not a wrong field) and lists the failures in
`validation_errors`. Then it replaces any full Aadhaar number in `extracted_fields` and in
`documents` with `XXXX-XXXX-<last four>` (RBI para 23 Explanation 1); the error string "Aadhaar
must be masked as XXXX-XXXX-1234" is the record that the file showed it in full.

**Output for case 24:** nothing to flag.

```json
"validation_errors": [],
"audit_log": [..., {"node": "validate", "errors": 0, "aadhaar_masked": false}]
```

(For case 14, the same node turns `"aadhaar": "9999-4512-7782"` into `"XXXX-XXXX-7782"` and
records `"aadhaar_masked": true` plus the error.)

**Syntax introduced:**

- `dict[str, tuple[Callable[[str], bool], str]]`: a dict from field name to a (function, message) pair; the function is called like any other.
- `[message for name, (check, message) in _CHECKS.items() if ...]`: a list comprehension that unpacks the tuple while looping.
- `datetime.strptime(text, "%d-%m-%Y").date()`: parse a date string; `date.today()`.
- `str | None` in a type hint: "a string or None" (Python 3.10+ union syntax).

## 3. retrieve_rules

**What it does.** `findings.py` turns the state into findings, each with a rule id, a sentence
and a retrieval query: R1 from `doc_type`, R2 from the PAN error or a missing PAN and Form 60,
R3/R5/R8 from validate's errors, R4 from exact date maths (`rules.kyc_due`: last KYC date + 2/8/10
years for high/medium/low), R7 from comparing `name` and `dob` across `documents` or a refusal
phrase in a branch note, and R6 only as a *tentative* hint when the name looks like a
placeholder. `rules_index.py` holds the Directions: `PyPDFLoader` reads `data/*.pdf` page by
page, `split_by_paragraph` cuts at `N.`, `(n)`, `(roman)` and numbered `Explanation N:` lines
(numbers must increase by one, so a stray number inside a sentence is not a paragraph) and
stamps every chunk with its citation, sub-chunks get their paragraph's opening sentence in
front, `RecursiveCharacterTextSplitter` cuts the long ones, `GoogleGenerativeAIEmbeddings`
turns chunks into vectors and `Chroma` stores them in `data/chroma/`. The node runs one
similarity search per query, keeps the top 3 chunks in `retrieved_rules` and the top-1
citation per query in `rbi_citations`.

**Output for case 24:**

```json
"findings": [{"rule": "R7",
              "text": "The name differs between documents in the file (passport: ANJALI MEHRA; pan_card: ANJALI MALHOTRA); CDD cannot be completed",
              "queries": ["open no account where the NBFC is unable to apply appropriate customer due diligence measures due to non-cooperation of the customer or unreliability of the documents; consider filing a suspicious transaction report"],
              "tentative": false,
              "details": {"field": "name", "values": [{"document": "passport", "value": "ANJALI MEHRA"}, {"document": "pan_card", "value": "ANJALI MALHOTRA"}],
                          "distinct": ["ANJALI MALHOTRA", "ANJALI MEHRA"], "mismatch": true}}],
"retrieved_rules": [{"rule": "R7", "rank": 0, "citation": "para 17(2)", "heading": "17. Without prejudice to the generality ...", "text": "17. Without prejudice ... (2) open no account where it is unable to apply appropriate CDD measures ..."},
                    {"rule": "R7", "rank": 1, "citation": "para 28(viii)", "text": "28. Simplified procedure for opening accounts ..."},
                    {"rule": "R7", "rank": 2, "citation": "para 17(3)", "text": "17. ... (3) not undertake a transaction ... without following the CDD procedure."}],
"rbi_citations": ["para 17(2)"],
"audit_log": [..., {"node": "retrieve_rules", "index": "chroma:rbi_nbfc_kyc_directions_2025_from_html.pdf:gemini-embedding-001",
                    "findings": ["R7"], "queries": 1, "chunks": 3, "citations": ["para 17(2)"]}]
```

**Syntax introduced:**

- `@dataclass` with `field(default_factory=list)`: a mutable default (a fresh list per instance); `asdict(obj)` turns it into a dict.
- `calendar.monthrange(year, month)[1]`: the last day of a month (29 Feb + 2 years lands on 28 Feb).
- `Document(page_content=..., metadata={...})`: LangChain's unit of text; splitters keep `metadata` on every piece.
- `PyPDFLoader(path).load()`: one `Document` per page.
- `RecursiveCharacterTextSplitter(chunk_size, chunk_overlap).split_documents(docs)`: cuts long text, keeps metadata.
- `GoogleGenerativeAIEmbeddings(model=...)`: text -> vector, through the Gemini API.
- `Chroma(collection_name, embedding_function, persist_directory)`, `.add_documents`, `.similarity_search(query, k)`: the vector store.
- `nonlocal lead` inside a nested function: lets the inner `flush()` assign to the outer variable.
- `shutil.rmtree(path)`: delete a folder tree (a half-built index).
- `json.dumps` / `json.loads` and `Path.write_text` / `read_text`: the build marker file.
- `time.sleep(seconds)` and `with_backoff(lambda: ..., what="...")`: pacing and retry; a `lambda` wraps the call so it can be retried.
- `try: ... except Exception as exc: ... raise`: catch, inspect, re-raise.

## 4. decide

**What it does.** Always computes the plain-code baseline, `rules_decide`: for every
non-tentative finding, look the rule up in `RULE_TABLE` (decision, citations), keep the most
serious decision (`escalate > re_kyc > approve`) and the citations in rule order. With
`DECIDE_MODE=llm`, `llm_decide` sends a system prompt (the policy in words) and one human
message (as-of date, masked fields, validation errors, the retrieved paragraphs) to a Gemini
Flash model at temperature 0 and asks for a `Verdict` (decision, citations, one-line reason) as
structured output; `keep_retrieved_citations` then drops any citation that was not retrieved,
and all citations when the decision is approve. With `DECIDE_MODE=agent` (`agent.py`), the model
gets tools instead and chooses: `search_rbi_rules` (the same index; its results become
citable), `check_kyc_due` (exact date maths), `compare_across_documents` (every document's
value for a field) and `submit_verdict`, which ends the loop; at most 5 investigation calls, and
a verdict is only recorded after the date check and both comparisons have been made. Every
tool call is logged in the audit entry. If the model cannot be reached, the baseline stands and
the audit says `llm_error`.

**Output for case 24** (rules mode):

```json
"decision": "escalate",
"reason": "The name differs between documents in the file (passport: ANJALI MEHRA; pan_card: ANJALI MALHOTRA); CDD cannot be completed",
"rbi_citations": ["para 17(2)"],
"rules_verdict": {"decision": "escalate", "citations": ["para 17(2)"], "reason": "...", "rules": ["R7"]},
"audit_log": [..., {"node": "decide", "mode": "rules", "rules_baseline": "escalate", "rules_fired": ["R7"],
                    "decision": "escalate", "citations": ["para 17(2)"]}]
```

In agent mode on the same case (live, `gemini-3.1-flash-lite`) the audit entry also held
`"llm_calls": 2` and three tool calls: `check_kyc_due(high, 01-04-2026)` -> due 01-04-2028, not
overdue; `compare_across_documents(name)` -> mismatch true; `compare_across_documents(dob)` ->
mismatch false; then `submit_verdict(escalate, ["para 17(2)"], ...)`.

**Syntax introduced:**

- `Literal["approve", "re_kyc", "escalate"]` on a Pydantic field: only those strings are accepted.
- `ChatGoogleGenerativeAI(model=..., temperature=0, thinking_budget=0, max_retries=1)`: the LangChain wrapper for Gemini.
- `model.with_structured_output(Verdict)`: the reply comes back as a `Verdict` object, not free text.
- Messages as tuples `("system", text)`, `("human", text)`, or objects `SystemMessage`, `HumanMessage`, `AIMessage`, `ToolMessage(content, tool_call_id)`.
- `@tool` on a function: its name, type hints and docstring become the tool description the model sees; `tool.invoke({"arg": value})` runs it.
- A function that defines functions (`make_tools`): closures that can see `state`, `index` and `retrieved` without being passed them.
- `model.bind_tools(tools, tool_choice="any")`: the model must call a tool; `tool_choice="submit_verdict"` forces that one.
- `response.tool_calls`: a list of `{"name", "args", "id"}` the model asked for; you run them and answer with a `ToolMessage` carrying the same id.
- `for _ in range(n): ... break`: a bounded loop with an early exit.
- `raise ValueError(...)` / `raise RuntimeError(...)`: fail loudly on an impossible result.

## 5. human_review

**What it does.** Calls `interrupt(payload)`. LangGraph stops the run there, saves the state
through the checkpointer under the thread id, and returns the payload to the caller (in the
result under `"__interrupt__"`). When someone calls `graph.invoke(Command(resume={...}), config)`
with the same thread id, the node runs again from the top, `interrupt()` now returns the resume
dict, and the node applies it: `action` confirm or override, an optional new `decision` and
`note`, and `auto: true` when the evals resumed it. `approve` never reaches this node (the
conditional edge after decide).

**Output for case 24** (resumed by the evals):

```json
"decision": "escalate",
"audit_log": [..., {"node": "human_review", "reviewer": "eval-auto", "action": "confirm",
                    "decision_before": "escalate", "decision": "escalate", "auto": true}]
```

**Syntax introduced:**

- `from langgraph.types import interrupt, Command`; `review = interrupt(payload)`: pause here, resume with the value given later.
- `Command(resume=value)` as the graph input: continue the paused run; the value is what `interrupt()` returns.
- `MemorySaver()` / `SqliteSaver(sqlite3.connect(path, check_same_thread=False))`: where paused state lives.
- `builder.compile(checkpointer=...)` and `config={"configurable": {"thread_id": ...}}`: a checkpointer needs a thread id per run.
- `builder.add_conditional_edges("decide", route_after_decide, {"human_review": "human_review", "end": END})`: a function picks the next node by name.
- `graph.get_state(config).next`: which node the paused run will resume at (`("human_review",)`), empty when finished.
- `graph.stream(input, config, stream_mode=["updates", "values"])`: yields `(mode, chunk)` pairs; an `updates` chunk with key `"__interrupt__"` means it paused.
- `interrupt(...) or {}`: `x or default` gives the default when x is falsy.

## 6. LangSmith

**What it does.** `tracing.py` builds one LangSmith `Client` with an anonymizer: a function the
SDK applies to every string in traced inputs, outputs and errors, which rewrites 12-digit runs
to `XXXX-XXXX-<last four>`. `run_config()` attaches a `LangChainTracer` using that client, so
every node run lands in project `kyc-compliance-agent` with masked data.
`evals/langsmith_dataset.py` uploads the cases as a dataset (masked documents, labels adjusted
for the four cases whose only difference was a full Aadhaar number) and runs
`langsmith.evaluate` over it with the scorecard's per-case scores.

**Syntax introduced:**

- `create_anonymizer(replacer)`: `replacer(text, path)` is called on each string in the data and returns the replacement.
- `Client(api_key=..., anonymizer=...)` and `LangChainTracer(project_name=..., client=...)`: the tracer is passed in `config["callbacks"]`.
- `evaluate(target, data=dataset_name, evaluators=[...], experiment_prefix=..., metadata=..., client=...)`: run `target(inputs)` on every example and score it.
- An evaluator is `def f(run, example) -> {"key": ..., "score": ...}`; `run.outputs` is what target returned, `example.outputs` the labels.
- `client.has_dataset`, `create_dataset`, `create_examples(inputs=..., outputs=..., dataset_id=...)`, `client.flush()`.

## 7. The evals (`evals/run_evals.py`)

For every case the runner streams the graph, keeps the `extract` node's own output (so
extraction is scored before validate masks the Aadhaar number) and the final state, resumes
the pause automatically, and also runs the regex reader alone. It prints decision accuracy
(the decide node and the rules baseline), citation hit rate, extraction accuracy with and
without the model, validate accuracy on the expected and on the extracted fields, and a
per-field table split by the `unlabelled` tag. `evals/POLICY.md` is the source of every label.

- `graph.stream(..., stream_mode=["updates", "values"])`: `updates` gives each node's return value, `values` the whole state.
- `os.environ.get("EVAL_CASES", "")`: an environment variable with a default; `EVAL_GOLD_FIELDS=1` runs the nodes by hand on the expected fields.
- `tuple[int, int]` scores `(hits, total)` added up with a tiny `_add` helper, then `hits / total` once at the end.
