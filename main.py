import logging
from pprint import pprint

from kyc_agent.graph import build_graph
from kyc_agent.state import KYCState

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    initial: KYCState = {
        "raw_document": "Name: Test Customer\nPAN: ABCDE1234F\nAadhaar: XXXX-XXXX-1234",
        "extracted_fields": {},
        "validation_errors": [],
        "rbi_citations": [],
        "decision": "",
        "audit_log": [],
    }
    final = build_graph().invoke(initial)
    pprint(final)


if __name__ == "__main__":
    main()
