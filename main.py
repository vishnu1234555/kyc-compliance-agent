import logging
from pprint import pprint

from langgraph.types import Command

from kyc_agent.graph import build_graph, run_config
from kyc_agent.state import KYCState

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# A medium-risk file last updated in 2015: periodic updation (8 years) is overdue, so the
# graph decides re_kyc and pauses for a reviewer. Fake data.
SAMPLE = """KYC RECORD - PERIODIC REVIEW
Customer ID: NB-00001
Risk category: Medium
Last KYC date: 14-02-2015

[Document 1: Passport]
REPUBLIC OF INDIA
Passport No: N0000000
Name: ASHA RAMAN
Date of birth: 15/08/1990
Address: 14 Kamaraj Street, Salem, Tamil Nadu
PIN: 636001

[Document 2: PAN card]
Permanent Account Number: ABCPE1234F
Name: ASHA RAMAN
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
        "reason": "",
        "rules_verdict": {},
        "audit_log": [],
    }
    graph = build_graph()
    config = run_config(thread_id="demo-NB-00001")
    result = graph.invoke(initial, config=config)
    if "__interrupt__" in result:
        # The graph is paused at human_review; a real reviewer would see this payload in a UI.
        print("PAUSED FOR REVIEW:")
        pprint(result["__interrupt__"][0].value)
        review = {"reviewer": "demo", "action": "confirm", "note": "checked in main.py"}
        result = graph.invoke(Command(resume=review), config=config)
    pprint({key: result[key] for key in ("decision", "reason", "rbi_citations", "audit_log")})


if __name__ == "__main__":
    main()
