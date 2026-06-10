from types import SimpleNamespace

from feynman_loop.retrieval.query_expansion import ClaudeQueryExpander, _Expansion


class _FakeMessages:
    def __init__(self, query):
        self._query = query
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(parsed_output=_Expansion(query=self._query))


class _FakeClient:
    def __init__(self, query):
        self.messages = _FakeMessages(query)


def test_expands_concept_into_query():
    client = _FakeClient("initial public offering: company first selling shares to the public")
    expander = ClaudeQueryExpander(client=client)
    q = expander.expand(concept_label="IPO")
    assert "public offering" in q
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5"  # cheap model, by design


def test_falls_back_to_label_when_empty():
    expander = ClaudeQueryExpander(client=_FakeClient("   "))
    assert expander.expand(concept_label="IPO") == "IPO"
