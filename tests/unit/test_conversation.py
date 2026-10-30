from laptop_server.agent.conversation import ConversationManager, estimate_tokens


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_estimate_tokens_counts_tamil_heavier():
    assert estimate_tokens("வணக்கம்") > estimate_tokens("vanakkam")


def test_session_creation_and_reuse():
    cm = ConversationManager()
    s = cm.get_or_create(None)
    assert len(s.id) == 32
    assert cm.get_or_create(s.id) is s
    assert cm.get_or_create("custom").id == "custom"
    assert len(cm) == 2


def test_ttl_expiry():
    clock = Clock()
    cm = ConversationManager(ttl_s=60, clock=clock)
    s = cm.get_or_create("a")
    clock.t = 61
    assert cm.get_or_create("a") is not s


def test_lru_eviction():
    cm = ConversationManager(max_sessions=2)
    cm.get_or_create("a")
    cm.get_or_create("b")
    cm.get_or_create("a")  # touch a
    cm.get_or_create("c")  # evicts b
    assert cm.reset("b") is False
    assert cm.reset("a") is True


def test_build_messages_trims_oldest_pairs_to_budget():
    cm = ConversationManager(max_turns=50, token_budget=200)
    s = cm.get_or_create("t")
    for i in range(30):
        cm.record_exchange(s, f"question {i} " + "x" * 80, f"answer {i} " + "y" * 80)
    msgs = cm.build_messages(s, "system prompt", "latest question")
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "latest question"}
    history = msgs[1:-1]
    assert history and len(history) % 2 == 0
    assert history[0]["role"] == "user"
    assert history[-1]["content"].startswith("answer 29")
    assert sum(estimate_tokens(m["content"]) for m in msgs) <= 200


def test_max_turns_caps_memory():
    cm = ConversationManager(max_turns=3, token_budget=10_000)
    s = cm.get_or_create("t")
    for i in range(10):
        cm.record_exchange(s, f"q{i}", f"a{i}")
    assert len(s.messages) == 6
    assert s.messages[0]["content"] == "q7"
