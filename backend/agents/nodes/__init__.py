"""All 11 agent nodes, re-exported for the graph builders.

Agent map (plan §2):
  #1 supervisor          common.supervisor
  #2 input_router        input_nodes.input_router
  #3 transcribe          input_nodes.transcribe          (Whisper)
  #4 process_file        input_nodes.process_file        (File Processor)
  #5 parse               input_nodes.parse               (Parser)
  #6 validate            input_nodes.validate            (Validator)
  #7 enrich              common.enrich                   (Enrichment)
  #8 geo                 advisor_nodes.geo               (Geo & Opportunity)
  #9 news                advisor_nodes.news              (News Aggregator)
  #10 advisor            advisor_nodes.advisor           (Advisor)
  #11 scenario_*         scenario_nodes.*                (Scenario Simulator)
"""
from agents.nodes.advisor_nodes import (
    advisor,
    geo,
    news,
    notify_user,
    portfolio_loader,
    save_advice,
)
from agents.nodes.common import enrich, supervisor
from agents.nodes.input_nodes import (
    human_review,
    input_router,
    parse,
    process_file,
    route_input,
    save_to_db,
    transcribe,
    validate,
)
from agents.nodes.scenario_nodes import (
    comparison,
    portfolio_simulator,
    scenario_parser,
)

__all__ = [
    # common
    "supervisor",
    "enrich",
    # input graph
    "input_router",
    "route_input",
    "transcribe",
    "process_file",
    "parse",
    "validate",
    "human_review",
    "save_to_db",
    # advisor graph
    "portfolio_loader",
    "geo",
    "news",
    "advisor",
    "save_advice",
    "notify_user",
    # scenario graph
    "scenario_parser",
    "portfolio_simulator",
    "comparison",
]
