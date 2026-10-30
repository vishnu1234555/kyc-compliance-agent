import logging
from pprint import pprint

from kyc_agent.graph import build_graph
from kyc_agent.state import KYCState

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

SAMPLE = """KYC RECORD - PERIODIC REVIEW
Customer ID: NB-00001
Risk category: Medium
Last KYC date: 14-02-2022

[Document 1: Passport]
REPUBLIC OF INDIA
Passport No: N0000000
Name: TEST CUSTOMER
Date of birth: 15/08/1990
Address: 14 Kamaraj Street, Salem, Tamil Nadu
PIN: 636001

[Document 2: PAN card]
Permanent Account Number: ABCPE1234F
Name: TEST CUSTOMER
Date of Birth: 15-08-1990
"""


def main() -> None:
    initial: KYCState = {
        "raw_document": SAMPLE,
        "extracted_fields": {},
        "documents": [],
        "validation_errors": [],
        "findings": [],
        "retrieved_rules": [],
        "rbi_citations": [],
        "decision": "",
        "audit_log": [],
    }
    final = build_graph().invoke(initial)
    pprint(final)


if __name__ == "__main__":
    main()
