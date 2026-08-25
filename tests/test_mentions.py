from backend.agent import find_mentioned_agents, should_offer_tools


def _agent(name: str) -> dict:
    return {"name": name, "system_prompt": "", "model": "x", "channel_scope": None}


def test_mention_order_follows_text_not_registration():
    agents = [_agent("ledger"), _agent("swarm")]
    hits = find_mentioned_agents("hey @swarm then @ledger", agents)
    assert [a["name"] for a in hits] == ["swarm", "ledger"]


def test_mention_is_whole_word():
    agents = [_agent("swarm")]
    assert find_mentioned_agents("see @swarmy please", agents) == []
    assert find_mentioned_agents("see @swarm please", agents)[0]["name"] == "swarm"


def test_mention_case_insensitive():
    agents = [_agent("swarm")]
    hits = find_mentioned_agents("Hi @SWARM", agents)
    assert len(hits) == 1


def test_no_mention():
    assert find_mentioned_agents("nobody here", [_agent("swarm")]) == []


def test_tools_skipped_for_small_talk():
    assert not should_offer_tools([{"author_kind": "human", "body": "@swarm hi"}])
    assert not should_offer_tools([{"author_kind": "human", "body": "hey @swarm how are you"}])


def test_tools_offered_for_file_or_history_asks():
    assert should_offer_tools([{"author_kind": "human", "body": "@swarm ls the sandbox"}])
    assert should_offer_tools([{"author_kind": "human", "body": "@swarm search history for shipping"}])
    assert should_offer_tools([{"author_kind": "human", "body": "@swarm remember the ship date"}])
    assert should_offer_tools([{"author_kind": "human", "body": "@swarm open gmail and screenshot the inbox"}])
    assert should_offer_tools([{"author_kind": "human", "body": "@swarm navigate to the docs"}])
