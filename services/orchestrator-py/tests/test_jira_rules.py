import datetime as dt

from app.agile.jira_sync import (
    changed_locally_since_sync, inbound_status, issue_fields, map_type, needs_pull, parse_ts,
)

T = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)


def test_type_mapping():
    assert map_type("Story") == "story" and map_type("Epic") == "epic" and map_type("Sub-task") == "task"
    assert map_type("Technical Task") == "task" and map_type("Improvement") == "story"
    assert map_type("Initiative") is None and map_type("") is None


def test_inbound_status_only_moves_done_and_in_progress():
    assert inbound_status("done", "new") == "done" and inbound_status("done", "in_progress") == "done"
    assert inbound_status("inprogress", "in_sprint") == "in_progress"
    assert inbound_status("inprogress", "ready") == "ready"                     # Jira cannot pull work into progress outside a sprint
    assert inbound_status("todo", "done") == "ready" and inbound_status("todo", "in_progress") == "in_sprint"
    assert inbound_status("todo", "refined") == "refined" and inbound_status("unknown", "ready") == "ready"
    assert inbound_status("todo", None) == "new"


def test_issue_fields_validate_instead_of_guessing():
    f = issue_fields({"key": "S-1", "summary": "  Pay   now ", "description": "d", "acceptanceCriteria": [" a ", "a", "b"],
                      "labels": ["x"] * 30, "storyPoints": 3})
    assert f["title"] == "Pay now" and f["acceptance_criteria"] == ["a", "b"] and f["estimate"] == 3.0 and len(f["labels"]) == 20
    assert issue_fields({"key": "S-2", "summary": "", "storyPoints": 2.3})["estimate"] is None       # not a multiple of 0.5
    assert issue_fields({"key": "S-3", "summary": "t", "storyPoints": -1})["estimate"] is None
    assert issue_fields({"key": "S-4", "summary": "x" * 500})["title"] == "x" * 200
    assert issue_fields({"key": "S-5", "summary": ""})["title"] == "S-5"                              # never an empty title


def test_pull_decision_uses_the_jira_revision():
    item = {"jira_updated": T}
    assert needs_pull(item, {"updated": (T + dt.timedelta(minutes=1)).isoformat()})
    assert not needs_pull(item, {"updated": T.isoformat()})
    assert not needs_pull(item, {"updated": (T - dt.timedelta(hours=3)).isoformat()})                 # overlap window re-reads
    assert needs_pull({"jira_updated": None}, {"updated": T.isoformat()}) and needs_pull(item, {"updated": None})


def test_local_change_detection_has_a_tolerance():
    base = {"jira_synced_at": T, "updated_at": T + dt.timedelta(seconds=1)}
    assert not changed_locally_since_sync(base)
    assert changed_locally_since_sync({**base, "updated_at": T + dt.timedelta(minutes=1)})
    assert not changed_locally_since_sync({"jira_synced_at": None, "updated_at": T})


def test_timestamp_parsing():
    assert parse_ts("2026-03-01T12:00:00Z") == T and parse_ts("2026-03-01T12:00:00+00:00") == T
    assert parse_ts("2026-03-01T12:00:00").tzinfo is not None and parse_ts("nope") is None and parse_ts(None) is None
