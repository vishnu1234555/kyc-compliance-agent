from kyc_agent.documents import classify, doc_type_of, has_form60, split_document

FILE = (
    "KYC RECORD - PERIODIC REVIEW\nCustomer ID: NB-10001\nRisk category: Medium\nLast KYC date: 14-02-2022\n\n"
    "[Document 1: Passport]\nREPUBLIC OF INDIA\nName: MEENAKSHI RAGHAVAN\n\n"
    "[Document 2: PAN card]\nINCOME TAX DEPARTMENT\nAKLPR4821Q\n"
)
UNTITLED = (
    "KYC RECORD - PERIODIC REVIEW\nCustomer ID: NB-10002\nRisk category: Low\nLast KYC date: 03-07-2020\n\n"
    "Government of India\nUnique Identification Authority of India\nArvind Kulkarni\nXXXX XXXX 4321\n\n"
    "INCOME TAX DEPARTMENT\nPermanent Account Number Card\nBXMPK7730L\n"
)


def test_header_and_blocks_with_titles() -> None:
    header, blocks = split_document(FILE)
    assert header == {"customer_id": "NB-10001", "risk_category": "Medium", "last_kyc_date": "14-02-2022"}
    assert [block.kind for block in blocks] == ["passport", "pan_card"]
    assert blocks[0].title == "Document 1: Passport"
    assert blocks[0].body.startswith("REPUBLIC OF INDIA")
    assert FILE[blocks[1].start : blocks[1].end] == blocks[1].text  # offsets point into the file


def test_untitled_blocks_are_classified_from_their_headings() -> None:
    _, blocks = split_document(UNTITLED)
    assert [block.kind for block in blocks] == ["aadhaar", "pan_card"]
    assert blocks[0].title is None


def test_doc_type_skips_pan_card_form60_and_notes() -> None:
    _, blocks = split_document(FILE)
    assert doc_type_of(blocks) == "passport"
    assert not has_form60(blocks)
    only_pan = split_document("KYC RECORD\nCustomer ID: X\n\n[Document 1: PAN card]\nAB\n\n[Document 2: Form No. 60]\nFORM NO. 60\n")[1]
    assert doc_type_of(only_pan) is None
    assert has_form60(only_pan)


def test_classify_titles_and_headings() -> None:
    assert classify("Document 1: Electricity bill", "") == "utility_bill"
    assert classify("Document 1: National Population Register letter", "") == "npr_letter"
    assert classify("Branch note dated 12-09-2026", "Customer refused to provide PAN or Form 60") == "note"
    assert classify(None, "FORM NO. 60\nDeclaration to be made by a person who does not have a Permanent Account Number") == "form60"
    assert classify("Document 1: Pension Payment Order", "Aadhaar: 9999 7788 1122") == "pension_order"
    assert classify(None, "Nothing recognisable") == "unknown"
