from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from kyc_agent import nodes
from kyc_agent.state import KYCState


def build_graph() -> CompiledStateGraph:
    builder: StateGraph = StateGraph(KYCState)
    builder.add_node("extract", nodes.extract)
    builder.add_node("validate", nodes.validate)
    builder.add_node("retrieve_rules", nodes.retrieve_rules)
    builder.add_node("decide", nodes.decide)
    builder.add_node("human_review", nodes.human_review)

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_edge("validate", "retrieve_rules")
    builder.add_edge("retrieve_rules", "decide")
    builder.add_edge("decide", "human_review")
    builder.add_edge("human_review", END)
    return builder.compile()
