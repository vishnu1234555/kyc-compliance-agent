# data/

## rbi_nbfc_kyc_directions_2025_from_html.pdf

The text of the **Reserve Bank of India (Non-Banking Financial Companies – Know Your Customer)
Directions, 2025** (RBI/DOR/2025-26/361, 28 Nov 2025, updated as on 29 Dec 2025).

Provenance, so nobody mistakes this file for the official PDF:

- The official PDF is <https://rbidocs.rbi.org.in/rdocs/notification/PDFs/361MD1E2F8EA063454AD5AFA1D02A1BA5ACA7.PDF>
  (linked from <https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12943>). On 30-10-2026 the
  build session could not download it: `rbidocs.rbi.org.in` closed the connection to the
  session's HTTP client without a response, while `rbi.org.in` itself answered normally.
- So this file was rendered (LibreOffice, headless) from the full text of the Directions as
  published on that rbi.org.in page, retrieved 30-10-2026: title, reference numbers, table of
  contents, Introduction, paragraphs 1 to 76 and the amendment footnote. Paragraph numbering
  and wording are the page's; only page breaks and fonts differ from the official PDF.
- Annex I and Annex II are separate PDFs on rbidocs (`361MD28112025_A1.pdf`, `_A2.pdf`) and
  are not included.

To use the official PDF instead, download it to this folder and point `RBI_PDF_PATH` at it (or
give it this file's name). `retrieve_rules` splits whichever file it is given by paragraph
number, so nothing else changes. Delete `data/chroma/` afterwards so the index is rebuilt.
