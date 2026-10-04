import asyncio

from app.repos.aws import DynamoStore


class FakeTable:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def query(self, **kw):
        self.calls.append(kw)
        i = len(self.calls) - 1
        page = {"Items": self.pages[i]}
        if i < len(self.pages) - 1:
            page["LastEvaluatedKey"] = {"PK": "x", "SK": f"PHASE#{i}"}
        return page


def test_list_phase_states_follows_every_page():
    store = DynamoStore.__new__(DynamoStore)
    store.phase_table = FakeTable([[{"SK": "PHASE#1"}, {"SK": "PHASE#2"}], [{"SK": "PHASE#3"}], [{"SK": "PHASE#4"}]])
    items = asyncio.run(store.list_phase_states("p"))
    assert [i["SK"] for i in items] == ["PHASE#1", "PHASE#2", "PHASE#3", "PHASE#4"]
    assert "ExclusiveStartKey" not in store.phase_table.calls[0]
    assert store.phase_table.calls[1]["ExclusiveStartKey"] == {"PK": "x", "SK": "PHASE#0"}
    assert store.phase_table.calls[2]["ExclusiveStartKey"] == {"PK": "x", "SK": "PHASE#1"}


def test_single_page_costs_one_query():
    store = DynamoStore.__new__(DynamoStore)
    store.phase_table = FakeTable([[{"SK": "PHASE#1"}]])
    assert len(asyncio.run(store.list_phase_states("p"))) == 1 and len(store.phase_table.calls) == 1
