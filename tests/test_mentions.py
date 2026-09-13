from backend.agent import find_mentioned_agents, should_offer_tools


def _agent(name: str) -> dict:
    return {"name": name, "system_prompt": "", "model": "x", "channel_scope": None}


def test_mention_parsing():
    agents = [_agent("ledger"), _agent("swarm")]
    # Order follows the text, not registration.
    hits = find_mentioned_agents("hey @swarm then @ledger", agents)
    assert [a["name"] for a in hits] == ["swarm", "ledger"]
    # Whole-word, case-insensitive, no false positives.
    assert find_mentioned_agents("see @swarmy please", agents) == []
    assert find_mentioned_agents("see @swarm please", agents)[0]["name"] == "swarm"
    assert len(find_mentioned_agents("Hi @SWARM", agents)) == 1
    assert find_mentioned_agents("nobody here", agents) == []


def test_should_offer_tools():
    small_talk = [{"author_kind": "human", "body": "@swarm hi"}]
    assert not should_offer_tools(small_talk)
    assert not should_offer_tools([{"author_kind": "human", "body": "hey @swarm how are you"}])
    assert not should_offer_tools([{"author_kind": "human", "body": "thanks!"}])
    # Rooms default to work: anything that isn't clear smalltalk gets tools,
    # so asking a bot to do something actually runs instead of just chatting.
    for body in ("@swarm ls the sandbox", "@swarm search history for shipping",
                 "@swarm remember the ship date", "@swarm open gmail and screenshot the inbox",
                 "@swarm navigate to the docs",
                 "@swarm summarize this week", "@swarm what's blocking the release?",
                 "@swarm draft the update", "@swarm plan the release",
                 "hey @swarm can you summarize the thread?"):
        assert should_offer_tools([{"author_kind": "human", "body": body}])
    # Channel kind gates plain small talk.
    bare_hi = [{"author_kind": "human", "body": "hi"}]
    assert should_offer_tools(bare_hi, channel_kind="dm")
    assert not should_offer_tools(bare_hi, channel_kind="room")
    assert should_offer_tools(bare_hi, channel_kind="group")
