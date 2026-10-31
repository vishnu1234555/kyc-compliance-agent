# Learn the code: every file, what calls what, every piece of syntax

Read the files in this order. For each one: what it does in two lines, which functions call
which (inside this repository), and every piece of syntax it uses, one line each. Python
basics (`def`, `if`, `for`, `return`, `import`) are not repeated; everything beyond them is.
`A -> B` means "A calls B".

How the pieces fit: `state.py` is the shared dictionary every node reads and writes. The five
nodes live in `nodes.py` and are wired together in `graph.py`. Each node leans on one or two
helper modules (extract on `extraction.py`, decide on `decide.py` and `agent.py`, and so on).
`evals/run_evals.py` runs the whole graph over the 30 cases and prints the scorecard.

---

## 1. `src/kyc_agent/state.py`

What it does: declares `KYCState`, the one dictionary that flows through the graph: the raw
document in, the extracted fields, the per-document fields, validation errors, findings,
retrieved paragraphs, citations, decision, reason, the rules verdict and the audit log.
Nothing else may carry data between nodes.

Calls: none (declarations only).

Syntax:
- `class KYCState(TypedDict)`: a dict with declared keys and value types; a type checker reads it, Python at run time does not enforce it.
- `Decision = Literal["approve", "re_kyc", "escalate", ""]`: a type that allows only these exact strings.
- `list[dict[str, Any]]`: a type hint for a list of dicts with string keys and any values.

## 2. `src/kyc_agent/config.py`

What it does: reads every setting from environment variables (and a `.env` file through
`load_dotenv`) into one frozen `Settings` object: keys, model ids, thresholds, paths, modes.
`get_settings()` is the only way the rest of the code reads configuration.

Calls: `get_settings -> Settings, _optional_int`.

Syntax:
- `from dotenv import load_dotenv` and `load_dotenv()`: copies KEY=VALUE lines from a `.env` file into the environment.
- `@dataclass(frozen=True) class Settings`: a class whose constructor and `==` are generated from the field list; `frozen` makes instances read-only.
- `os.getenv("NAME", "default")`: an environment variable, or the default when unset.
- `Path(__file__).resolve().parents[2]`: the file's own path, made absolute, then two folders up (the repository root).
- `REPO_ROOT / "data" / "chroma"`: `/` joins path parts.
- `int | None` and `value if cond else other`: a hint for "int or None" and the one-line if/else expression.
- `float(...)`, `int(...)`: convert the environment strings.

## 3. `src/kyc_agent/ocr.py`

What it does: repairs OCR digit confusions (O->0, I/l->1) inside runs of digit-like
characters that already contain a real digit, so "7O42" becomes "7042" while "OIL" stays.
Runs before any regex looks at the text.

Calls: `fix_digit_noise -> _fix_run` (through `re.sub`).

Syntax:
- `str.maketrans({"O": "0", ...})` and `run.translate(table)`: a character-to-character replacement table and its application.
- `re.compile(r"[0-9OIl]{2,}")`: a compiled regular expression; `r"..."` is a raw string so backslashes stay as written.
- `_DIGIT_RUN.sub(_fix_run, text)`: replace every match, calling a function with each match object.
- `match.group()`: the matched text.
- `any(char.isdigit() for char in run)`: True if at least one character is a digit (a generator expression inside `any`).

## 4. `src/kyc_agent/documents.py`

What it does: cuts a KYC file into the record header and one `Block` per document (blocks are
separated by blank lines) and names each block's kind (passport, pan_card, form60, note, ...)
from its title or first lines. Also answers "which document is the identity document" and
"is there a Form 60".

Calls: `split_document -> parse_header, classify, Block`; `classify` and `parse_header` use compiled patterns; `doc_type_of` and `has_form60` read the blocks.

Syntax:
- `@dataclass(frozen=True) class Block`: an immutable record with `index, start, text, title, kind`.
- `@property def end(self)`: a method used like an attribute (`block.end`, no parentheses).
- `re.compile(pattern, re.I | re.M)`: flags combined with `|`; `re.I` ignores case, `re.M` makes `^`/`$` work per line.
- `pattern.search(text)`, `pattern.match(text)`, `match.group(1)`: find anywhere / at the start; the first captured group.
- `re.split(r"(\n[ \t]*\n+)", text)`: split on blank lines and keep the separators (the capturing group), so offsets can be tracked.
- `text.split("\n", 1)[1]`: split at the first newline only, take the part after it.
- `[line for line in body.split("\n") if line.strip()][:3]`: list comprehension with a filter, then a slice of the first three.
- `list[tuple[str, re.Pattern[str]]]`: a hint for a list of (name, compiled pattern) pairs; `for kind, pattern in _KIND_RULES` unpacks each pair.
- `str | None`: an optional string.
- `logger = logging.getLogger(__name__)` and `logger.debug(...)`: a named logger; `%s` placeholders are filled lazily.

## 5. `src/kyc_agent/arbitration.py`

What it does: merges candidates from both readers. A candidate that fails its label's structure
check (PAN shape, Aadhaar Verhoeff digit, masked Aadhaar allowed) is dropped; overlapping
survivors are resolved by "what the document labels itself" first, then label specificity,
then span length, then confidence. Ported from the Aadhaar-Final project.

Calls: `validate_and_arbitrate -> is_structurally_valid, arbitrate`; `arbitrate -> _overlaps, _rank`; `_rank -> priority`; `is_structurally_valid -> VALIDATORS[label]` (`_valid_aadhaar -> _digits, verhoeff_valid`; `_valid_account -> _digits`).

Syntax:
- `from __future__ import annotations`: lets type hints mention names before they are defined.
- `class Candidate(TypedDict, total=False)`: a dict shape where every key may be missing.
- `from collections.abc import Callable, Iterable`: hints for "a function" and "anything you can loop over".
- Nested tuples `_D = ((0, 1, ...), ...)` indexed as `_D[checksum][...]`: the Verhoeff multiplication and permutation tables.
- `for i, ch in enumerate(reversed(digits))`: index and value, walking the string from the end.
- `str.isdigit()`, `re.sub(r"\D", "", text)`: digit test; remove every non-digit.
- `re.fullmatch(pattern, text)`: the whole string must match.
- `set("ABCFGHJLPT")`: a set of characters; `t[3] in _PAN_HOLDER_TYPES` is a membership test.
- `dict[str, Callable[[str], bool]]`: a dict from label to a function taking a string and returning a bool; `check(text)` calls it.
- `LABEL_PRIORITY.get(label, _DEFAULT_PRIORITY)`: a dict lookup with a default.
- `def _rank(entity) -> tuple[int, int, int, float]` and `sorted(candidates, key=_rank)`: sort by a tuple, element by element; negative numbers put the largest first.
- `entity.get("trusted")`, `float(entity.get("confidence") or 0.0)`: tolerant reads of optional keys; `x or 0.0` replaces None with 0.0.
- `any(_overlaps(cand, k) for k in kept)`: generator inside `any`.
- `{id(e) for e in kept}`: a set comprehension of object identities.
- `{**cand, "reason": "..."}`: a new dict that copies `cand` and adds a key.
- `-> tuple[list[Candidate], list[Candidate]]` and `return kept, rejected`: returning two values as a tuple.
- `f"failed {cand['label']} structure check"`: an f-string, expressions inside `{}`.

## 6. `src/kyc_agent/regex_reader.py`

What it does: the first reader. Finds labelled lines (`Name:`, `PIN Code:`, `Aadhaar No:`),
ID numbers by shape (PAN, masked and full Aadhaar), dates on their own line, the bare PAN on a
PAN card, a six-digit PIN on its own line and relative names ("S/O ..."), each as a
`Candidate` with its offsets. Names and addresses without a label are left to the model.

Calls: `regex_candidates -> _labelled_lines, _sweeps, _inside_an_address`; `_labelled_lines -> _candidate`; `_sweeps -> _candidate`.

Syntax:
- A dict of label -> pattern text, then a dict comprehension `{label: re.compile(rf"...{words}...", re.I | re.M) for label, words in _LINE_LABELS.items()}`: builds one compiled pattern per label; `rf"..."` is a raw f-string.
- `(?P<value>.+?)`, `match.group("value")`, `match.start("value")`: a named group, its text and its start offset; `.+?` is the shortest match.
- `pattern.finditer(text)`: every match in order.
- `_VALUE_SHAPE.get(label)` then `shape.search(value)`: a second pattern trims the value to the part that has the right shape.
- `out.append(...)`, `value[: trailing.start()].rstrip(" ,")`: list append; slice up to an offset and strip trailing characters.
- `for block in blocks: if block.kind != "pan_card": continue`: skip to the next loop item.
- `(cand["label"], cand["start"], cand["end"])` as a dict key: tuples can be dict keys; used to drop duplicates.
- `sorted(found, key=lambda c: (c["start"], c["label"]))`: `lambda` is a one-expression function written inline.
- `any(a["start"] <= cand["start"] and cand["end"] <= a["end"] for a in addresses)`: containment test over a list.

## 7. `src/kyc_agent/pii_model.py`

What it does: the second reader. Wraps the fine-tuned GLiNER2 model: loads it once on the
first use, asks it for Person Name, Address, Aadhaar Number and PAN Number spans with a
{label: description} prompt, and turns its output into `Candidate`s. Also `NullReader` (model
off), `FakeReader` (tests), the revision/load-time record and a tokenizer shim.

Calls: `default_reader -> get_settings, GLiNERReader / NullReader`; `GLiNERReader.read -> _load, parse_model_output`; `GLiNERReader._load -> load_gliner2, resolve_revision`; `parse_model_output -> locate` (through the nested `add`); `FakeReader.read -> locate`.

Syntax:
- `class PIIReader(Protocol)`: an interface by shape; any object with `name`, `read` and `describe` fits, no inheritance needed.
- `class FakeReader` with `def __init__(self, spans)`: a constructor storing data on `self`.
- `text.find(value)`: the offset of the first occurrence, or -1.
- `def add(label, match) -> None` defined inside `parse_model_output`: a nested function that can see the outer `out` list and `text`.
- `isinstance(match, dict)`, `isinstance(start, int)`: run-time type checks on the model's output.
- `str(match.get("text", "")).strip()`, `text[start:end].lstrip()`: slice a string by offsets, strip spaces.
- `self._model: Any = None` and `self.revision: str | None = None`: attributes declared with hints.
- `time.perf_counter()`: a precise clock for timing the load; `round(x, 1)`.
- `from gliner2 import GLiNER2` written inside a function: a lazy import, so tests never load torch.
- `GLiNER2.from_pretrained(model_id, map_location="cpu")`: load the weights from the Hugging Face hub or a folder onto the CPU.
- `model.extract_entities(text, labels, threshold=..., include_confidence=True, include_spans=True)`: the GLiNER2 call; `labels` is a dict of label -> description.
- `huggingface_hub.snapshot_download(model_id, local_files_only=True)`: the local cache folder of the model; its last path part is the commit sha.
- `os.path.isdir(...)`, `os.path.basename(os.path.normpath(...))`: path tests and the last path component.
- `DebertaV2TokenizerFast.from_pretrained(repo_or_dir, extra_special_tokens={})`: the fallback tokenizer load, when transformers 4 cannot read metadata written by transformers 5.
- `gliner_base.load_extractor_tokenizer = load_tokenizer`: replacing a library function with our own at run time (the shim).
- `try: ... except ValueError as exc: if "..." not in str(exc): raise`: catch one error type, re-raise if it is not the known one.
- `@lru_cache(maxsize=1) def default_reader()`: the function body runs once; later calls return the same object.
- `settings.pii_model.strip().lower() in ("", "off", "none")`: membership in a tuple of strings.

## 8. `src/kyc_agent/schema.py`

What it does: the Pydantic contract for extract's output: the ten fields with their formats
(dates `DD-MM-YYYY`, Aadhaar `XXXX-XXXX-1234` or `9999-1234-5678`, doc_type from a fixed list).
`as_state_fields()` turns it into the dict that goes into the state, with missing fields left out.

Calls: none inside the repository (Pydantic validates on construction).

Syntax:
- `class ExtractedFields(BaseModel)`: a Pydantic model; constructing it validates every field.
- `model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)`: unknown keys are an error; strings are stripped.
- `dob: str | None = Field(default=None, pattern=r"^\d{2}-\d{2}-\d{4}$")`: an optional field that must match a pattern when present.
- `doc_type: DocType | None` with `DocType = Literal[...]`: only the listed values are accepted.
- `self.model_dump(exclude_none=True)`: the model as a plain dict without the None fields.

## 9. `src/kyc_agent/extraction.py`

What it does: where the two readers meet. Fixes OCR noise, splits the file, collects regex
candidates over the whole text and model candidates per document, runs arbitration, picks each
document's fields, takes every file-level field from the first document that shows it, adds
doc_type, form60 and the header values, and checks the result against the schema.

Calls: `extract_fields -> fix_digit_noise, split_document, regex_candidates, _model_candidates, validate_and_arbitrate, fields_for_block, doc_type_of, has_form60, normalise_date, ExtractedFields, Extraction`; `fields_for_block -> _rank, _NORMALISE[name]` (the `normalise_*` functions), `block.contains`; `_model_candidates -> reader.read`.

Syntax:
- `@dataclass class Extraction` with `stats: dict[str, Any] = field(default_factory=dict)`: a mutable default made fresh per instance.
- `re.fullmatch(r"\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*", text or "")` and `match.groups()`: all captured groups as a tuple; `text or ""` turns None into an empty string.
- `day, month, year = (int(part) for part in match.groups())`: unpack a generator into three names.
- `f"{day:02d}-{month:02d}-{year:04d}"`: zero-padded numbers in an f-string.
- `re.sub(r"\s+", " ", text).strip(" .,:-").upper()`: collapse spaces, strip edge punctuation, uppercase.
- `_NORMALISE = {"name": normalise_name, ..., "pan": lambda text: text.strip().upper()}`: functions stored in a dict and called later as `_NORMALISE[name](cand["text"])`.
- `sorted((c for c in inside if c["label"] == label), key=_rank)`: sort a filtered generator.
- `for name, label in _LABEL_FOR.items()`: loop over key/value pairs.
- `cand["text"].split("\n", 1)[0]`: keep only the first line of a model span.
- `{**cand, "text": text, ...}`: copy a dict and override keys.
- `ExtractedFields(**fields)`: unpack a dict into keyword arguments.
- `logger.info("extract: %d documents %s", len(blocks), [b.kind for b in blocks])`: lazy formatting with `%d` and `%s`.

## 10. `src/kyc_agent/validators.py`

What it does: the deterministic field checks (PAN shape, masked Aadhaar, PIN shape, date of
birth valid and 18 or over) and `mask_aadhaar`, which turns any 12-digit number into
`XXXX-XXXX-<last four>`. No I/O, no model.

Calls: `is_valid_dob` uses `datetime.strptime` and `date.today`; the rest are pattern checks.

Syntax:
- `_PAN.fullmatch(pan) is not None`: a pattern check returned as a bool.
- `datetime.strptime(dob, "%d-%m-%Y").date()`: parse a date string in a given format, keep the date part.
- `try: ... except ValueError: return False`: an impossible date (31-02) raises and is treated as invalid.
- `today = today or date.today()`: use the given date, or today's.
- `(today.month, today.day) < (born.month, born.day)`: tuple comparison, element by element; subtracting the bool adjusts the age.
- `digits[-4:]`: the last four characters.

## 11. `src/kyc_agent/rules.py`

What it does: the exact arithmetic behind the rules: `kyc_due` adds 2/8/10 years to the last
KYC date (high/medium/low risk) and says whether it is overdue on the as-of date;
`compare_across_documents` lists one field's value in every document and whether they disagree.
Both are also tools the agent can call.

Calls: `kyc_due -> parse_date, add_years`.

Syntax:
- `calendar.monthrange(year, month)[1]`: the number of days in that month (29 February plus two years lands on 28 February).
- `day.replace(year=year, day=min(day.day, last_day))`: a copy of a date with some parts changed.
- `date | None = None` default argument and `today = as_of or date.today()`.
- `return {"error": f"..."}` early: a dict with an error message instead of raising, so a tool can show it to the model.
- `(today - due).days`, `max(..., 0)`: the difference between two dates in days, floored at 0.
- `sorted({str(entry["value"]) for entry in values})`: a set comprehension removes duplicates, then sorted into a list.
- `document.get(field) not in (None, "")`: skip missing or empty values.

## 12. `src/kyc_agent/findings.py`

What it does: turns the state into a list of `Finding`s, one per rule whose trigger the
deterministic code can see (R1 from doc_type, R2 from PAN, R3/R5/R8 from validation errors, R4
from date maths, R7 from document comparison or a refusal note, R6 only as a tentative hint),
each with the retrieval query for its paragraph.

Calls: `build_findings -> _finding, kyc_due, compare_across_documents, looks_like_placeholder`; `_finding -> Finding`; `Finding.as_dict -> dataclasses.asdict`.

Syntax:
- `@dataclass class Finding` with `queries: list[str] = field(default_factory=list)` and `def as_dict(self): return asdict(self)`: a record with a list default and a dict export.
- `QUERIES: dict[str, list[str]]`: the retrieval queries per rule, looked up with `QUERIES[rule]`.
- `re.findall(r"[A-Z]+", name.upper())`: every run of letters as a list.
- `not tokens or any(token in _PLACEHOLDER_WORDS for token in tokens)`: short-circuit boolean logic.
- `def _finding(rule, text, tentative=False, **details)`: `**details` collects extra keyword arguments into a dict.
- `_finding("R4", f"...", **due)`: `**due` spreads a dict into keyword arguments.
- `doc_type.replace("_", " ")`, `"; ".join(f"..." for entry in values)`: string replace and join over a generator.
- `for document in documents: ... break`: stop at the first refusal note.
- `_REFUSAL.search(note)`: a compiled pattern with alternatives (`|`) and a non-capturing group `(?:...)`.

## 13. `src/kyc_agent/backoff.py`

What it does: `with_backoff` runs a function and, on a rate limit or outage, waits 5, 15, 30,
60, 60, 60 seconds between retries, then tries once more. `error_kind` says whether an
exception is a spent daily quota (stop), a transient error (retry) or something else.

Calls: `with_backoff -> is_transient`; `error_kind -> is_transient`.

Syntax:
- `T = TypeVar("T")` and `def with_backoff(call: Callable[[], T], ...) -> T`: a generic hint: the result type is whatever the passed function returns.
- `for attempt, wait in enumerate(schedule)`: index and value of a tuple of seconds.
- `try: return call() except Exception as exc: if not is_transient(exc): raise`: retry only the known-transient errors; `raise` alone re-raises the current exception.
- `time.sleep(wait)`: pause.
- `any(mark in text for mark in _TRANSIENT_MARKS)`: substring tests over a tuple.

## 14. `src/kyc_agent/rules_index.py`

What it does: the RBI Directions as a searchable index. Loads the PDF page by page, cuts it at
paragraph numbers so every chunk carries a `para 42(1)`-style citation, splits long paragraphs
further, embeds the chunks with Gemini and stores them in Chroma on disk (built once, paced for
the free tier). `ChromaIndex.search` and `FakeIndex.search` answer queries.

Calls: `default_index -> get_settings, ChromaIndex`; `ChromaIndex.search -> _open, with_backoff`; `ChromaIndex._open -> build_chunks, _expected_marker, _marker, add_in_batches, _embeddings`; `build_chunks -> load_pages, split_by_paragraph, split_long_chunks`; `split_by_paragraph -> flush (nested), citation_for`; `add_in_batches -> with_backoff`.

Syntax:
- `from langchain_community.document_loaders import PyPDFLoader` and `PyPDFLoader(str(pdf_path)).load()`: one LangChain `Document` per PDF page, with `metadata["page"]`.
- `from langchain_core.documents import Document` and `Document(page_content=..., metadata={...})`: LangChain's text unit.
- `RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=150).split_documents(chunks)`: cut long documents into overlapping pieces, keeping metadata.
- `GoogleGenerativeAIEmbeddings(model=self.embedding_model)`: text to vectors through the Gemini API (the key comes from the environment).
- `Chroma(collection_name=..., embedding_function=..., persist_directory=...)`, `store.add_documents(batch)`, `store.similarity_search(query, k=k)`, `store._collection.count()`: the vector store, adding chunks, nearest-neighbour search, how many are stored.
- `def flush() -> None:` with `nonlocal lead` inside `split_by_paragraph`: a nested function allowed to assign to the enclosing function's variable.
- `buffer.clear()`, `"\n".join(buffer).strip()`: empty a list in place; join lines.
- `roman_order.index(clause) + 1`, `stripped[:90]`: list index lookup; a slice.
- `int(para_match.group(1)) == int(para) + 1`: numbers must increase by one to start a new paragraph.
- `class RulesIndex(Protocol)` with `search(self, query, k)`: the interface both indexes follow.
- `re.findall(r"[a-z]+", query.lower())` and `len(words & set(...))`: word overlap with `&` (set intersection) in `FakeIndex`.
- `sorted(self.documents, key=overlap, reverse=True)`: descending sort by a nested scoring function.
- `self._cache: dict[tuple[str, int], list[Document]] = {}` and `if key not in self._cache:`: a per-process memo of search results.
- `with_backoff(lambda: self._open().similarity_search(query, k=k), what="query embedding")`: wrap a call in a lambda so it can be retried.
- `json.dumps(expected, indent=2)`, `json.loads(marker.read_text(encoding="utf-8"))`, `marker.write_text(...)`, `path.exists()`: the build marker file.
- `shutil.rmtree(self.persist_dir)`: delete a folder tree (a half-built index).
- `for start in range(0, len(chunks), batch_size): batch = chunks[start : start + batch_size]`: walk a list in fixed-size slices.
- `time.sleep(pause)`: pacing between embedding batches.
- `@lru_cache(maxsize=1) def default_index()`: one index object per process.

## 15. `src/kyc_agent/llm.py`

What it does: builds the Gemini chat model from settings (model name, temperature 0, thinking
budget, one client retry). The only place a chat model is constructed, so every node uses the
same settings.

Calls: `get_chat_model -> get_settings`.

Syntax:
- `from langchain_google_genai import ChatGoogleGenerativeAI` inside the function: lazy import.
- `ChatGoogleGenerativeAI(model=..., temperature=0, max_retries=1, thinking_budget=...)`: the LangChain chat model for Gemini; the API key is read from `GOOGLE_API_KEY`.
- `def get_chat_model(**overrides)` and `options.update(overrides)`: optional keyword overrides merged into a dict.

## 16. `src/kyc_agent/decide.py`

What it does: `rules_decide` applies the policy table to the findings (the baseline and the
fallback). `llm_decide` builds the prompt (as-of date, masked fields, validation errors,
retrieved paragraphs), makes one structured-output call for a `Verdict`, and
`keep_retrieved_citations` drops any citation that was not retrieved (and all of them for an
approve).

Calls: `rules_decide -> build_findings, Decision`; `llm_decide -> build_messages, with_backoff, keep_retrieved_citations, Decision`; `Decision.as_dict`.

Syntax:
- `RULE_TABLE: dict[str, tuple[str, list[str]]]` and `outcome, rule_citations = RULE_TABLE[rule]`: unpack the (decision, citations) pair.
- `SYSTEM_PROMPT = """..."""`: a multi-line string.
- `class Verdict(BaseModel)` with `decision: Literal["approve", "re_kyc", "escalate"]` and `citations: list[str] = Field(default_factory=list, description="...")`: the structured output the model must return; the description is shown to the model.
- `@dataclass class Decision` with `def as_dict(self) -> dict[str, Any]`: the internal result record.
- `SEVERITY[outcome] > SEVERITY[decision]`: comparing ranks from a dict.
- `json.dumps(state.get("extracted_fields", {}), indent=2)`: pretty JSON for the prompt.
- `"\n\n".join(f"[{chunk['citation']}] {chunk['text']}" for chunk in retrieved) or "(nothing retrieved)"`: a join, with a fallback when empty.
- `return [("system", SYSTEM_PROMPT), ("human", human)]`: LangChain messages as (role, text) tuples.
- `{chunk["citation"] for chunk in state.get("retrieved_rules", [])}`: a set comprehension of allowed citations.
- `model.with_structured_output(Verdict)` and `structured.invoke(messages)`: ask the model to answer in the `Verdict` shape; the reply is a `Verdict` object.
- `" ".join(verdict.reason.split())`: collapse all whitespace to single spaces.
- `date | None = None` and `(as_of or date.today()).strftime("%d-%m-%Y")`: format a date.

## 17. `src/kyc_agent/agent.py`

What it does: the tool-calling version of decide. Builds four tools bound to the current file,
lets Gemini choose among them (at most 5 investigation calls; a verdict is only accepted after
the date check and both document comparisons), ends with `submit_verdict`, records every call,
and filters citations to paragraphs that were retrieved by the node or by the agent's own searches.

Calls: `agent_decide -> make_tools, build_messages, with_backoff, missing_required_calls, _tool_result, _verdict_from_args, keep_retrieved_citations, Decision, AgentRun, ToolCallRecord`; `make_tools -> index.search, kyc_due, compare_across_documents` (inside the tool bodies); `_verdict_from_args -> Verdict`.

Syntax:
- `from langchain_core.tools import tool` and `@tool def search_rbi_rules(query: str) -> str:` with a docstring: the function becomes a tool; its name, argument types and docstring are what the model sees.
- Tools defined inside `make_tools(...)`: closures that see `state`, `index` and `retrieved` without being passed them.
- `tools_by_name = {t.name: t for t in tools}` and `tool.invoke(call["args"])`: run a tool by name with the model's arguments.
- `model.bind_tools(tools, tool_choice="any")`: the model must call a tool; `tool_choice="submit_verdict"` forces that one.
- `response.tool_calls`: a list of `{"name", "args", "id"}` the model asked for.
- `SystemMessage(content=...)`, `HumanMessage(content=...)`, `AIMessage`, `ToolMessage(content=result, tool_call_id=call["id"])`: message objects; a tool result is answered with the same id.
- `messages: list[BaseMessage] = [...]` and `messages.append(response)`: the growing conversation.
- `AGENT_INSTRUCTIONS.format(budget=max_tool_calls)`: `str.format` with a named placeholder.
- `for _ in range(max_tool_calls + 3): ... break`: a bounded loop with an early exit; `_` is a throwaway name.
- `json.dumps(kyc_due(...))`: tool results are returned to the model as JSON text.
- `all(record.args.get(k) == v for k, v in args.items())`: every required argument matches.
- `", ".join(f"{k}={v!r}" for k, v in args.items()) or "..."`: `!r` shows the repr of a value.
- `try: ... except Exception as exc: return f"error: {type(exc).__name__}: {exc}"`: a bad argument is reported back to the model, not raised.
- `raise RuntimeError(...)`, `raise ValueError(...)`: fail loudly on an impossible result.
- `@dataclass class AgentRun` with `tool_calls: list[ToolCallRecord] = field(default_factory=list)`: the run record.

## 18. `src/kyc_agent/tracing.py`

What it does: LangSmith tracing, on only when `LANGSMITH_API_KEY` is set, through a `Client`
whose anonymizer rewrites every 12-digit run in traced data to `XXXX-XXXX-<last four>`.
`mask_text` is the one masking function reused by the checkpoint serializer and the dataset upload.

Calls: `tracing_callbacks -> langsmith_client, get_settings`; `langsmith_client -> tracing_enabled, make_anonymizer, get_settings`; `make_anonymizer -> _replacer -> mask_text`.

Syntax:
- `_TWELVE_DIGITS.sub(lambda match: f"XXXX-XXXX-{re.sub(r'[^0-9]', '', match.group())[-4:]}", text)`: a lambda as the replacement function, keeping the last four digits.
- `from langsmith.anonymizer import create_anonymizer` and `create_anonymizer(_replacer)`: LangSmith walks nested inputs/outputs and calls `_replacer(text, path)` on every string.
- `Client(api_key=..., anonymizer=...)`: the LangSmith client with the anonymizer attached.
- `LangChainTracer(project_name=..., client=client)`: a LangChain callback that sends runs to that client; returned in a list for `config["callbacks"]`.
- `@lru_cache(maxsize=1) def langsmith_client()`: one client per process; `langsmith_client.cache_clear()` resets it in tests.
- `bool(get_settings().langsmith_api_key)`: an empty key means off.

## 19. `src/kyc_agent/checkpoints.py`

What it does: the two checkpointers (in memory, SQLite) built with a `MaskingSerializer`, so
every checkpoint, pending write and channel blob is masked when it is written. LangGraph's
bookkeeping keys and UUID strings are left untouched so the graph can still find its checkpoints.

Calls: `memory_checkpointer -> MaskingSerializer`; `sqlite_checkpointer -> MaskingSerializer`; `MaskingSerializer.dumps_typed -> mask_data -> mask_text`.

Syntax:
- `frozenset({...})`: an immutable set of key names.
- `def mask_data(value, key=None)` calling itself: recursion over dicts, lists, tuples and sets; `isinstance(value, (list, tuple))`-style checks pick the branch.
- `{k: mask_data(v, k if isinstance(k, str) else None) for k, v in value.items()}`: a dict comprehension passing the key down.
- `class MaskingSerializer(JsonPlusSerializer)` with `def dumps_typed(self, obj): return super().dumps_typed(mask_data(obj))`: subclass LangGraph's serializer and change one method; `super()` calls the parent's version.
- `MemorySaver(serde=MaskingSerializer())`, `SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=...)`: the two savers with the custom serializer; `check_same_thread=False` lets LangGraph use the connection from its worker threads.
- `-> tuple[str, bytes]`: the serializer returns a type tag and the encoded bytes.

## 20. `src/kyc_agent/nodes.py`

What it does: the five node functions, each `KYCState -> KYCState`, each appending one audit
entry: `extract` (both readers), `validate` (checks, then masks the Aadhaar number),
`retrieve_rules` (findings -> queries -> citations), `decide` (rules baseline, then the LLM or
the agent, with a fallback to the rules), `human_review` (pause with `interrupt`).

Calls: `extract -> default_reader, extract_fields`; `validate -> _masked -> mask_aadhaar`, and the `_CHECKS` functions (`is_valid_pan`, ...); `retrieve_rules -> build_findings, default_index, index.search, get_settings`; `decide -> get_settings, rules_decide, llm_decide / agent_decide, get_chat_model, default_index, error_kind, Decision`; `human_review -> interrupt`.

Syntax:
- `_CHECKS: dict[str, tuple[Callable[[str], bool], str]]` and `for name, (check, message) in _CHECKS.items()`: a dict of (function, message) pairs, unpacked while looping.
- `[message for name, (check, message) in _CHECKS.items() if name in fields and not check(str(fields[name]))]`: the list of failing checks.
- `{**state, "validation_errors": errors, "audit_log": [*state["audit_log"], entry]}`: a new state dict; `[*old, new]` copies a list and appends.
- `state.get("documents", [])`: a tolerant read with a default.
- `{key: result.stats[key] for key in (...)}`: a dict comprehension picking keys.
- `next((entry.get("customer_id") for entry in state["audit_log"] if entry.get("node") == "extract"), None)`: the first matching item, or None.
- `(citation, hit.page_content) not in seen_chunks` with `seen_chunks: set[tuple[str, str]] = set()`: a set of pairs to drop duplicates.
- `for rank, hit in enumerate(hits)`: index and item.
- `try: ... except Exception as exc:` around the LLM call, writing `entry["llm_error"]` and `entry["llm_error_kind"] = error_kind(exc)`: the failure is recorded, the run continues on the rules verdict.
- `f"{type(exc).__name__}: {str(exc)[:200]}"`: the exception's class name and the first 200 characters of its text.
- `from langgraph.types import interrupt` and `review = interrupt({...}) or {}`: pause the graph here; on resume the call returns the value given to `Command(resume=...)`.
- `review.get("decision") in ("approve", "re_kyc", "escalate")`: validate the reviewer's input.
- `f"{reason} Reviewer: {review['note']}".strip()`: build the updated reason.

## 21. `src/kyc_agent/graph.py`

What it does: wires the nodes into the LangGraph `StateGraph` (START -> extract -> validate ->
retrieve_rules -> decide -> human_review or END), compiles it with a checkpointer, and gives
every run its config (a thread id for the checkpointer, plus the LangSmith tracer when on).

Calls: `build_graph -> default_checkpointer`; `default_checkpointer -> get_settings, sqlite_checkpointer / memory_checkpointer`; `run_config -> tracing_callbacks`; `route_after_decide` is called by LangGraph after `decide`.

Syntax:
- `StateGraph(KYCState)`, `builder.add_node("extract", nodes.extract)`, `builder.add_edge(START, "extract")`, `builder.add_edge("human_review", END)`: the graph definition.
- `builder.add_conditional_edges("decide", route_after_decide, {"human_review": "human_review", "end": END})`: a function returns a key, the dict maps it to the next node.
- `builder.compile(checkpointer=...)` returning a `CompiledStateGraph`: the runnable graph with saved state between steps.
- `def route_after_decide(state) -> str: return "end" if ... else "human_review"`: the routing function.
- `str(uuid.uuid4())`: a random unique id for a run.
- `{"configurable": {"thread_id": ...}, "callbacks": [...]}`: the LangGraph run config shape.

## 22. `evals/run_evals.py`

What it does: runs the 30 cases, scores decision, citations, extraction (with and without the
model, before masking), validate (on the expected and on the extracted fields), prints the
scorecard and writes `evals/last_run.json`. Caches LLM/agent answers per case, resumes from the
cache, stops cleanly on a daily-quota 429 and prints the resume command.

Calls: `main -> get_settings, mode_name, cache_dir_for, select_cases, load_cases, build_graph, run_all, summarise, default_reader().describe, print_scorecard, resume_command, langsmith_client`; `run_all -> run_case`; `run_case -> mode_name, read_cached, final_from_cache / extraction_only / run_on_gold_fields / run_graph, extract_fields(NullReader), write_cached, is_daily_quota, score_*`; `run_graph -> fresh_state, run_config, graph.stream`; `final_from_cache -> nodes.extract, fresh_state`; `extraction_only -> nodes.extract, nodes.validate, rules_decide`; `run_on_gold_fields -> nodes.extract, drop_nulls, nodes.validate, nodes.retrieve_rules, nodes.decide`; `summarise -> _add, per_field_table, safe_divide`; `print_scorecard -> has_miss, _ratio`; `score_fields -> score_each_field -> drop_nulls`; `score_validate -> score_validate_on -> validate`.

Syntax:
- `Score = tuple[int, int]`: a type alias for (hits, total).
- `with path.open(encoding="utf-8") as handle: return json.load(handle)`: a context manager closes the file for you.
- `{key: value for key, value in fields.items() if value is not None}`: a filtering dict comprehension.
- `hits / total if total else 0.0`: avoid division by zero.
- `for mode, chunk in graph.stream(graph_input, config=config, stream_mode=["updates", "values"])`: LangGraph streams `(mode, chunk)` pairs; an `updates` chunk keyed `"__interrupt__"` means the graph paused.
- `from langgraph.types import Command` and `graph_input = Command(resume=AUTO_REVIEW)`: resume a paused run with a value.
- `{**run_config(), **(config or {})}`: merge two dicts, the second winning.
- `os.environ.get("EVAL_CASES", "")`, `wanted.split(",")`, `case_id.strip()`: a comma-separated environment variable to a set of ids.
- `cache_dir / f"{case_id}.json"`, `path.exists()`, `cache_dir.mkdir(parents=True, exist_ok=True)`, `.write_text(...)`: file handling with `pathlib`.
- `datetime.now(timezone.utc).isoformat(timespec="seconds")`: a UTC timestamp string.
- `next(entry for entry in final["audit_log"] if entry.get("node") == "decide")`: the first matching entry (raises if none); the `(..., {})` form returns a default instead.
- `decide_entry.get("llm_error_kind") == "daily_quota" or "PerDay" in str(...)`: the stop test.
- `row = run_case(...); rows.append(row); if row["quota_stop"] and not stopped: stopped = True`: state carried across loop iterations.
- `[call for call in decide_entry.get("tool_calls", []) if call.get("tool") != "submit_verdict"]`: count investigation calls only.
- `table[field][reader][split] = _add(table[field][reader][split], score)`: nested dict updates with a tiny helper that adds two (hits, total) pairs.
- `f"{field:<14} {_ratio(model['all']):<8}"`: f-string alignment (`<14` pads to 14 characters, left-aligned).
- `print(...)`: allowed here, for the scorecard only.
- `LAST_RUN_PATH.write_text(json.dumps({"pii_model": reader, **summary}, indent=2) + "\n", encoding="utf-8")`: write the summary as JSON.
- `if __name__ == "__main__": main()`: run `main` only when the file is executed as a script.

## 23. `evals/split.py`

What it does: the fixed tune/test split of the 30 cases. `make_split` sorts the ids within each
tag group and alternates the halves (unlabelled starts with tune, labelled with test), giving
15/15 with 9 vs 8 unlabelled; `write_split` saves it as `evals/split.json`; `load_split` reads it.

Calls: `write_split -> make_split`.

Syntax:
- `sorted(case["id"] for case in cases if "unlabelled" in case.get("tags", []))`: sort a filtered generator.
- `unlabelled[0::2]` and `labelled[1::2]`: slices with a step of 2 (every other item, starting at 0 or at 1).
- `json.dumps({"rule": "...", **split}, indent=2)`: a dict literal with another dict spread into it.
- `Path(__file__).resolve().parent / "split.json"`: a path next to this file.

## 24. `evals/sweep_threshold.py`

What it does: loads the model once, and for each threshold counts extraction hits on the tune
half, the test half and all 30 cases. Chooses the threshold on the tune half only (ties go to
the higher value) and prints the test-half number next to the full-30 "tuned on itself" number.

Calls: `main -> load_cases, load_split, get_settings, GLiNERReader, extract_fields, score_each_field, choose, reader.describe`.

Syntax:
- `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`: put the repository root first on the import path, so `evals.run_evals` imports when the file runs as a script.
- `from collections import Counter` and `hits[half][name] += hit`: a dict that starts every missing key at 0.
- `[float(value) for value in sys.argv[1:]] or [0.2, 0.3, ...]`: command-line arguments, or defaults when none.
- `max(results, key=lambda threshold: (results[threshold][0], threshold))`: the key with the largest (hits, threshold) tuple.
- `reader.threshold = threshold`: changing an attribute between runs instead of reloading the model.
- `f"{threshold:.2f}"`, `f"{value:.4f}"`: fixed decimals in an f-string.
- `abs(a - b) < 1e-9`: float comparison with a tolerance.

## 25. `evals/model_check.py`

What it does: one case (case 24 by default) through extraction with the model off and on,
printing the file's name field and every document's name side by side, with the model id, the
Hugging Face revision that was loaded and the load time. A look, not a test.

Calls: `main -> load_cases, get_settings, GLiNERReader, NullReader, extract_fields, model.describe`.

Syntax:
- `sys.argv[1] if len(sys.argv) > 1 else "case-24"`: an optional command-line argument.
- `next(case for case in load_cases() if case["id"] == case_id)`: find one case by id.
- `for left, right in zip(regex_only.documents, with_model.documents)`: walk two lists in step.
- `f"{'name (file)':<28} {str(...):<26}"`: aligned columns.

## 26. `evals/langsmith_dataset.py`

What it does: uploads the cases as a LangSmith dataset with the documents masked (so the four
R3 cases get the labels of the masked file, originals kept alongside) and runs
`langsmith.evaluate` over it with the scorecard's three per-case scores. Needs
`LANGSMITH_API_KEY`; not run in the budget-limited sessions.

Calls: `main -> langsmith_client, load_cases, upload_dataset, get_settings, build_graph, evaluate(target, ...)`; `target -> run_graph`; `upload_dataset -> client.has_dataset / read_dataset / create_dataset / create_examples, examples_from_cases`; `examples_from_cases -> labels_for_masked_input, mask_text`; `labels_for_masked_input -> fired_rules, mask_text, RULE_TABLE`; evaluators `decision_match`, `citation_hit_rate -> score_citations, safe_divide`, `extraction_accuracy -> score_fields, safe_divide`.

Syntax:
- `case["why"].split(":", 1)[0]` and `prefix.split("+")`: parse "R2+R4: ..." into rule ids.
- `from langsmith import evaluate` and `evaluate(target, data=dataset.name, evaluators=[...], experiment_prefix=..., metadata={...}, max_concurrency=1, client=client)`: run `target(inputs)` on every example and score it.
- `def target(inputs: dict) -> dict:` defined inside `main`: a nested function that sees `graph`.
- Evaluator signature `def decision_match(run, example) -> dict` returning `{"key": ..., "score": ...}`; `run.outputs` is what target returned, `example.outputs` the labels.
- `client.has_dataset(dataset_name=name)`, `client.create_dataset(...)`, `client.create_examples(inputs=..., outputs=..., dataset_id=...)`, `client.flush()`: the LangSmith dataset calls.
- `raise SystemExit("...")`: exit the script with a message.
- `"--upload-only" in sys.argv`: a command-line flag.

## 27. Data files under `evals/`

- `cases.json`: the 30 cases (raw document, expected fields, expected errors, decision, citations, why, tags). `POLICY.md` is the rule table behind every label. Do not edit by hand without reading POLICY.md section 5.
- `split.json`: the tune/test halves (made by `split.py`).
- `last_run.json`: the summary of the last scorecard run.
- `llm_cache/<mode>/<case_id>.json`: one cached LLM or agent answer per case; a rerun skips them.
- `scorecards/<mode>.txt`: the printed scorecard of the last run in that mode.

## 28. Not under those folders, but part of the loop

- `main.py`: one file through the graph; shows the pause payload and resumes with a demo confirm (`graph.invoke(..., config=run_config(...))`, `result["__interrupt__"][0].value`, `Command(resume={...})`).
- `tests/conftest.py`: pytest fixtures with `@pytest.fixture(autouse=True)` and `monkeypatch.setattr(...)` / `monkeypatch.setenv(...)`, which swap in the null reader, the fake index, rules mode and a blank LangSmith key for every test.
