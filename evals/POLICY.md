# Eval policy for the KYC compliance agent

As-of date for every label in `cases.json`: **30-10-2026**.

## 1. Where the rules come from

The 2016 RBI KYC Master Direction was repealed on 28 Nov 2025. For NBFCs the rules are now:

**Reserve Bank of India (Non-Banking Financial Companies – Know Your Customer) Directions, 2025**
RBI/DOR/2025-26/361, dated 28 Nov 2025, updated as on 29 Dec 2025
https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12943

The paragraph numbers below were read from that page by Vishnu on 30-10-2026. The Claude
session that wrote this file could not open rbi.org.in (the host is outside the session's
allowed network), so **none of the paragraph numbers have been independently checked**. Check
them against the page before relying on a citation in front of an auditor.

Paragraphs relied on (never cite a paragraph that is not on this list unless you read it in
the document yourself; if you add one, quote its sentence here):

| Paragraph | What it says (as read on 30-10-2026) |
|---|---|
| para 5(1)(xiv) | The six OVDs: passport, driving licence, proof of possession of Aadhaar number, Voter ID card, NREGA job card signed by a State Govt officer, NPR letter with name and address. A utility bill, property tax receipt, pension order or allotment letter counts for ADDRESS only; the customer must give an OVD with the current address within 3 months. |
| para 17(1) | No account in an anonymous or fictitious / benami name. |
| para 17(2) | No account where appropriate CDD cannot be applied; consider filing an STR. |
| para 23(3) | Proof of possession of Aadhaar (where offline verification is not possible) or any OVD. |
| para 23(5) | PAN, or its e-document equivalent, or Form No. 60. |
| para 23 Explanation 1 | The customer must redact or black out the Aadhaar number. |
| para 23 Explanation 4 | Aadhaar is not mandatory for KYC (except for some government benefits). No rule cites it; it is why files with no Aadhaar at all can still be approved. |
| para 42(1) | Periodic updation at least every 2 years (high risk), 8 years (medium), 10 years (low). |

## 2. What one case is

An existing customer's KYC file being re-checked. `raw_document` is plain text, as if OCR'd:

1. A record header written by the NBFC, always in this shape:
   ```
   KYC RECORD - PERIODIC REVIEW
   Customer ID: NB-10001
   Risk category: Medium
   Last KYC date: 14-02-2022
   ```
2. Then the text of the documents on file. Most start with a title like `[Document 1: Passport]`;
   three cases (02, 14, 21) have no titles at all, so the document type has to be read from
   the card's own headings ("Unique Identification Authority of India", "INCOME TAX DEPARTMENT").
   The identity document always comes first, then the PAN card or Form 60, then anything else.

## 3. Decision rules

Label every case from this table, never case by case. The most serious outcome wins:
**escalate > re_kyc > approve**. Citations are every fired rule's citations in rule order
(R1 first), with duplicates removed, written exactly as shown.

| Rule | Fires when | Decision | Citations |
|---|---|---|---|
| R1 | No OVD on file: the identity document is one of the address-only types, or there is no identity document at all | re_kyc | `para 5(1)(xiv)`, `para 23(3)` |
| R2 | No PAN and no Form 60, or the PAN on file is in the wrong format | re_kyc | `para 23(5)` |
| R3 | An Aadhaar number is visible in full anywhere in the file (not masked) | re_kyc | `para 23 Explanation 1` |
| R4 | Periodic updation overdue on the as-of date: 30-10-2026 is later than last KYC date + 2 years (high), 8 years (medium) or 10 years (low) | re_kyc | `para 42(1)` |
| R5 | PIN code on file is not six digits starting 1-9 | re_kyc | none |
| R6 | Anonymous, fictitious or benami-looking name (`Test Customer`, `XYZ`, `Demo User`) | escalate | `para 17(1)` |
| R7 | CDD cannot be completed: name or DOB differs between documents in the same file, or the file says the customer refused to give documents | escalate | `para 17(2)` |
| R8 | Under 18 on the as-of date | escalate | none (a lending rule, not in the KYC Directions) |
| none | Nothing fires | approve | none |

R5 and R8 have no citation, so a case where only they fire has `expected_citations: []` and is
left out of the citation hit rate.

## 4. Field conventions (`expected_fields`, and what `extract` must produce)

Keys, always all ten, `null` when the file does not show the value. A missing field is not a
wrong field: `validate` only checks the keys it is given, so `extract` must leave a key out
rather than send `None`.

| Key | Format | Notes |
|---|---|---|
| `name` | UPPERCASE, single spaces | As on the identity document. Uppercase so `XYZ` and PAN-card casing need no special rule. |
| `dob` | `DD-MM-YYYY` | Even when the document prints `15/08/1990`. |
| `pan` | 10 uppercase characters as printed | Fake PANs have `P` as the 4th letter. A wrong-format PAN is kept as printed so `validate` can flag it. |
| `form60` | `true` / `false` | Strict boolean, never null: `true` only when a Form No. 60 is in the file. |
| `aadhaar` | `XXXX-XXXX-1234` or `9999-1234-5678` | Normalised to 4-4-4 with hyphens. **`extract` must not mask an unmasked number**: R3 depends on `validate` seeing it. |
| `address` | Text as printed, without the PIN | The PIN is always printed on its own line, labelled or not. |
| `pincode` | 6-character string as printed | A wrong PIN is kept as printed so `validate` can flag it. |
| `doc_type` | one of the values below, or `null` | The first document after the header, ignoring PAN card and Form 60. |
| `risk_category` | `low` / `medium` / `high` | From the record header. |
| `last_kyc_date` | `DD-MM-YYYY` | From the record header. |

Allowed `doc_type` values:

- OVDs (para 5(1)(xiv)): `passport`, `driving_licence`, `aadhaar`, `voter_id`, `nrega_job_card`, `npr_letter`
- Address-only documents (R1 fires): `utility_bill`, `property_tax_receipt`, `pension_order`, `allotment_letter`
- `null`: no identity document on file

`extract` reads a 12-digit number as an Aadhaar number only when its Verhoeff check digit is
right (the same rule as the PII project's validator), and treats a masked number as valid.
`validate` still flags every unmasked number.

Each field is taken from the first document that shows it. When documents disagree (R7 cases)
the identity document's value is the expected one; the conflicting value only appears in `why`.

`expected_errors` is exactly what `kyc_agent.nodes.validate` returns for `expected_fields`
with the nulls dropped, in validate's own order (pan, aadhaar, pincode, dob). The test
`test_expected_errors_match_the_validate_node` enforces this.

## 5. The case file

`cases.json` holds 30 cases. Keys per case: `id`, `raw_document`, `expected_fields`,
`expected_errors`, `expected_decision`, `expected_citations`, `why`, `tags`. `tags` is a list
drawn from `unlabelled` (17 cases), `ocr_noise` (5), `no_titles` (3) and `unmasked_aadhaar`
(4); the scorecard's labelled/unlabelled split reads the `unlabelled` tag, never case ids.
`why` is one line and
starts with the rules that fired, `R2+R4: ...` or `none: ...`; the tests parse that prefix and
re-derive the decision and citations from the table above, so a mislabelled case fails CI.

Mix: 9 approve, 12 re_kyc, 9 escalate. Every rule fires in at least two cases; six cases
fire two or more rules at once.

| Case | Decision | Rules | doc_type | Unlabeled values | OCR noise | Notes |
|---|---|---|---|---|---|---|
| case-01 | approve | none | passport | PAN card | | no Aadhaar on file |
| case-02 | approve | none | aadhaar | Aadhaar + PAN, no document titles | | |
| case-03 | approve | none | voter_id | | | Form 60 instead of PAN |
| case-04 | approve | none | driving_licence | | | no Aadhaar; high risk, due 09-03-2028 |
| case-05 | approve | none | nrega_job_card | PAN card | PAN `l` for `1` | |
| case-06 | approve | none | npr_letter | Aadhaar + PAN | | |
| case-07 | approve | none | aadhaar | Aadhaar + PAN | Aadhaar `O` for `0` | high risk, due 18-05-2028 |
| case-08 | approve | none | passport | | | adult born 12-12-2006 |
| case-09 | approve | none | voter_id | PAN card | | |
| case-10 | re_kyc | R1 | utility_bill | PAN card | | |
| case-11 | re_kyc | R1 | property_tax_receipt | | | |
| case-12 | re_kyc | R2 | passport | PAN card | | PAN has 9 characters |
| case-13 | re_kyc | R2 | aadhaar | Aadhaar | | no PAN, no Form 60 |
| case-14 | re_kyc | R3 | aadhaar | Aadhaar + PAN, no document titles | | Aadhaar in full |
| case-15 | re_kyc | R4 | passport | | PIN `O` for `0` | high, last KYC 15-06-2023 |
| case-16 | re_kyc | R4 | voter_id | | DOB `O` for `0` | low, last KYC 20-01-2015 |
| case-17 | re_kyc | R5 | aadhaar | | | PIN `63200` |
| case-18 | re_kyc | R5 | driving_licence | | | PIN `011001` |
| case-19 | re_kyc | R2+R4 | nrega_job_card | PAN card | | PAN 8 characters; medium, last KYC 11-11-2016 |
| case-20 | re_kyc | R1+R3 | pension_order | | | Aadhaar printed in full on the PPO |
| case-21 | re_kyc | R3+R5 | aadhaar | Aadhaar + PAN, no document titles | | PIN `41100` |
| case-22 | escalate | R6 | aadhaar | Aadhaar + PAN | | `Test Customer` |
| case-23 | escalate | R6 | voter_id | | | `XYZ` |
| case-24 | escalate | R7 | passport | PAN card | | Mehra on passport, Malhotra on PAN |
| case-25 | escalate | R7 | aadhaar | Aadhaar + PAN | PAN `I` for `1` | DOB 1988 on Aadhaar, 1986 on PAN |
| case-26 | escalate | R1+R2+R7 | utility_bill | | | customer refused documents; dob null |
| case-27 | escalate | R8 | aadhaar | Aadhaar | | minor born 2012, Form 60 |
| case-28 | escalate | R8 | passport | | | minor born 2013, has PAN |
| case-29 | escalate | R4+R6 | driving_licence | | | `Demo User`; high, last KYC 09-09-2022 |
| case-30 | escalate | R3+R7 | aadhaar | Aadhaar + PAN | | Reddy on Aadhaar, Rao on PAN |

17 cases show at least one key value with no label next to it (the extractor is known to lean
on labels). 5 cases carry OCR noise, only where a careful human can still tell the true value;
`expected_fields` hold the true values.

Fake data only: invented names; PANs with `P` as the 4th letter; real Indian cities with
valid-format PINs; every unmasked Aadhaar starts with `9999`; passport, EPIC, DL, job-card and
PPO numbers are made up. No real person's details.

`validate` uses today's real date. DOBs and last-KYC dates were chosen so that no label flips
before 2028. The first to flip are the high-risk approve cases 04 (09-03-2028), 24 (01-04-2028)
and 07 (18-05-2028): move their `Last KYC date` forward before then.

## 6. Running the evals

```
.venv/bin/python evals/run_evals.py
```

No API keys, no network, no new dependencies. `LOG_LEVEL=INFO` adds one log line per case.
The script prints four numbers and a table of every case with a miss, then writes the numbers
to `evals/last_run.json` (committed, so a PR diff shows the before/after):

1. **decision accuracy**: cases whose `decision` exactly matches `expected_decision`, over all cases.
2. **citation hit rate**: per case, the share of `expected_citations` found in `rbi_citations`;
   averaged over the cases that have any expected citations (17 of 30).
3. **extraction accuracy**: non-null expected fields that `extracted_fields` got exactly right,
   pooled over all cases (279 fields in total).
4. **validate accuracy**: `nodes.validate` run on `expected_fields` with nulls dropped; share of
   cases whose error list equals `expected_errors`. This is 1.0 by construction and only drops if
   `validate` or the case file changes.

Since roadmap item 1 the scorecard also prints, for the same run:

5. **extraction, regex only**: extraction accuracy with the PII model switched off.
6. **validate on extracted**: `nodes.validate` run on extract's own output; share of cases whose
   error list equals `expected_errors`. Unlike number 4 this can drop when extraction is wrong.
7. **A per-field table**: hits/total per field, regex+model next to regex only, each split into
   the cases tagged `unlabelled` and the rest. The difference between the two halves is what
   the model adds.

Extraction is always scored on extract's own output, read from the graph stream before
`validate` masks the Aadhaar number.

Every PR that changes behaviour runs this before and after and reports both numbers.

## 7. Baseline (30-10-2026, skeleton at commit 49641a0, before any node is implemented)

`extract`, `retrieve_rules` and `decide` are still stubs, so the first three numbers are zero.
Exact output:

```
KYC EVALS - 30 cases
  decision accuracy    0/30 = 0.0000
  citation hit rate    0.0000 (mean over 17 cases with expected citations)
  extraction accuracy  0/279 = 0.0000
  validate accuracy    30/30 = 1.0000

Misses: 30 of 30 cases
```

(The miss table that follows lists all 30 cases with `decision expected -> ''`, `0/n` fields
and validate `ok`.)

## 8. Judgment calls made while building this

- **R1 covers "no identity document at all"**, not only "a non-OVD document". That is why the
  refusal case (26) fires R1 and R2 as well as R7. The decide node will see `doc_type:
  utility_bill`, `pan: null`, `form60: false`, so a mechanical rule reading is the safe one.
- **`doc_type` is the first document after the header**, ignoring PAN card and Form 60. With
  two OVDs on file (passport + Aadhaar card) the first one wins.
- **Names are UPPERCASE** rather than Title Case, so `XYZ` does not become `Xyz` and PAN-card
  casing needs no exception.
- **Address excludes the PIN.** Real cards print the PIN at the end of the address; here the PIN
  is always its own line so a noisy PIN never corrupts the address string.
- **`form60` is a strict boolean**, so it is always scored: the extractor must assert absence.
- **Unmasked Aadhaar stays unmasked in `expected_fields`** (formatted `9999-4512-7788`), and the
  roadmap entry for `extract` now says not to mask. Otherwise `validate` could never catch R3.
- **Extraction accuracy is pooled over fields**, not averaged per case, so a case with 10
  fields counts more than one with 7.
- **`validate` accuracy compares the whole error list in order.** validate's order is fixed
  (pan, aadhaar, pincode, dob), so this is deterministic.
- **The decision-mix test asserts minimums (8/11/8)** rather than exact counts, so a 31st case
  does not break it; the "exactly 30 cases" test is exact because the plan says 30.
- **`Demo User`** was added as a third fictitious name alongside `Test Customer` and `XYZ`.
- **The scorecard is 161 lines**, over the 150 target, because loops were kept plain instead of
  compressed into comprehensions.
- **`print` is allowed in `evals/run_evals.py` for the scorecard only**; CLAUDE.md now says so.
- **`pyproject.toml` gained `"."` on the pytest path and `evals/__init__.py` exists**, so the
  tests can import the scoring functions from the script.
- **Three unmasked Aadhaar numbers were changed by their last digit on 30-10-2026** so that
  they pass the Verhoeff check `extract` applies before it believes a 12-digit run is an
  Aadhaar number: case 14 `9999 4512 7788` -> `9999 4512 7782`, case 21 `9999 6543 2109` ->
  `9999 6543 2101`, case 30 `9999 3344 5566` -> `9999 3344 5565`, in `raw_document` and in
  `expected_fields`. Labels, errors and citations are unchanged. Case 20's `9999 7788 1122`
  already passed.
