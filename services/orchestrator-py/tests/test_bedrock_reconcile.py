"""Reconciling the token ledger with Bedrock's invocation logs (docs/bedrock-reconciliation.md)."""

from __future__ import annotations

import gzip
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.bedrock_reconcile import (
    BedrockReconciler, model_key, CloudWatchSource, FileSource, MARGIN, ReconcileScheduler, S3Source, build_recon_source, parse_invocation_record, reconcile,
)

DAY = date(2026, 10, 10)
NOON = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def log_record(rid: str, *, inp=100, out=20, cr=0, cw=0, ts: datetime = NOON, model="eu.anthropic.claude-test", role="arn:aws:sts::1:assumed-role/devmind/x", **extra: Any) -> dict[str, Any]:
    return {"schemaType": "ModelInvocationLog", "schemaVersion": "1.0", "timestamp": ts.isoformat().replace("+00:00", "Z"), "accountId": "1", "region": "eu-west-2",
            "requestId": rid, "operation": "InvokeModelWithResponseStream", "modelId": model, "identity": {"arn": role},
            "input": {"inputContentType": "application/json", "inputBodyJson": {"messages": []}, "inputTokenCount": inp, "cacheReadInputTokenCount": cr, "cacheWriteInputTokenCount": cw},
            "output": {"outputContentType": "application/json", "outputBodyJson": [], "outputTokenCount": out}, **extra}


def led(rid: str | None, *, inp=100, out=20, cr=0, cw=0, ts: datetime = NOON, est=False, project="p1", activity="stage.generate", model="m") -> dict[str, Any]:
    return {"provider_request_id": rid, "model": model, "ts": ts, "input_tokens": inp, "cache_read_tokens": cr, "cache_write_tokens": cw, "output_tokens": out,
            "estimated": est, "project_id": project, "activity": activity, "status": "ok"}


def aws(*records: dict[str, Any]) -> list:
    return [c for c in (parse_invocation_record(r) for r in records) if c]


# ----------------------------------------------------------------------------- parsing
def test_the_log_record_fields_become_a_call_and_only_the_counts_are_kept():
    c = parse_invocation_record(log_record("r1", inp=10, out=5, cr=900, cw=40))
    assert (c.request_id, c.input_tokens, c.cache_read, c.cache_write, c.output_tokens, c.model_id) == ("r1", 10, 900, 40, 5, "eu.anthropic.claude-test")
    assert not hasattr(c, "input") and "messages" not in repr(c)


def test_a_record_without_count_fields_falls_back_to_the_logged_usage_block():
    base = log_record("r2")
    base["input"].pop("inputTokenCount"); base["output"].pop("outputTokenCount")
    base["output"]["outputBodyJson"] = {"usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 1}}
    c = parse_invocation_record(base)
    assert (c.input_tokens, c.cache_read, c.cache_write, c.output_tokens) == (7, 50, 1, 3)
    stream = log_record("r3")
    stream["input"].pop("inputTokenCount"); stream["output"].pop("outputTokenCount")
    stream["output"]["outputBodyJson"] = [{"type": "message_start", "message": {"usage": {"input_tokens": 11, "cache_read_input_tokens": 4, "output_tokens": 1}}},
                                         {"type": "content_block_delta"}, {"type": "message_delta", "usage": {"output_tokens": 42}}]
    c = parse_invocation_record(stream)
    assert (c.input_tokens, c.cache_read, c.output_tokens) == (11, 4, 42)


def test_unusable_and_failed_records():
    assert parse_invocation_record({"requestId": "x"}) is None                     # no timestamp
    assert parse_invocation_record({"timestamp": NOON.isoformat()}) is None         # no request id
    nothing = log_record("r4"); nothing["input"] = {}; nothing["output"] = {}
    assert parse_invocation_record(nothing) is None                                 # cannot read tokens: reported as unreadable by the source
    failed = log_record("r5"); failed["input"] = {}; failed["output"] = {}; failed["errorCode"] = "ThrottlingException"
    c = parse_invocation_record(failed)
    assert c.failed and c.input_tokens == 0 and c.output_tokens == 0


# ----------------------------------------------------------------------------- the comparison
def run(ledger, calls, **kw):
    return reconcile(ledger, calls, day=DAY, threshold=0.02, now=LATER, **kw)


def test_identical_ledger_and_log_reconcile_with_no_drift_when_the_log_input_excludes_cache():
    ledger = [led("a", inp=100, cr=900, out=20), led("b", inp=50, out=5)]
    r = run(ledger, aws(log_record("a", inp=100, cr=900, out=20), log_record("b", inp=50, out=5)))
    assert (r.status, r.matched, r.mismatched, r.drift) == ("ok", 2, 0, 0.0)
    assert r.convention == "input_excludes_cache" and r.ledger_tokens == r.aws_tokens == 1075


def test_it_works_out_that_a_log_whose_input_count_already_includes_the_cache_is_the_same_thing():
    ledger = [led("a", inp=100, cr=900, out=20)]
    r = run(ledger, aws(log_record("a", inp=1000, cr=900, out=20)))             # 1000 = 100 uncached + 900 cached, reported as the input total
    assert (r.status, r.matched, r.convention) == ("ok", 1, "input_includes_cache")


def test_when_nothing_was_cached_the_two_readings_cannot_be_told_apart_and_that_is_said():
    r = run([led("a")], aws(log_record("a")))
    assert r.convention == "indeterminate" and r.status == "ok" and "could not be told apart" in r.note


def test_calls_only_one_side_has_are_listed_with_their_tokens():
    ledger = [led("only-ledger", inp=300, out=30), led("both")]
    r = run(ledger, aws(log_record("both"), log_record("only-aws", inp=2000, out=100)))
    assert (r.missing_in_ledger, r.missing_in_aws) == (1, 1)
    kinds = {d["kind"]: d for d in r.diffs}
    assert kinds["missing_in_ledger"]["aws_tokens"] == 2100 and kinds["missing_in_aws"]["ledger_tokens"] == 330
    assert r.diffs[0]["kind"] == "missing_in_ledger"                           # largest first


def test_a_token_mismatch_is_a_difference_and_a_drift_over_the_threshold_is_flagged():
    r = run([led("a", inp=1000, out=100)], aws(log_record("a", inp=800, out=100)))
    assert (r.mismatched, r.status) == (1, "drift") and r.drift == round((1100 - 900) / 900, 4)
    assert r.diffs[0] == {**r.diffs[0], "kind": "mismatch", "delta": 200, "project_id": "p1", "activity": "stage.generate"}
    small = run([led("a", inp=1001, out=100)] + [led(f"x{i}", inp=1000, out=0) for i in range(99)],
                aws(log_record("a", inp=1000, out=100), *[log_record(f"x{i}", inp=1000, out=0) for i in range(99)]))
    assert small.status == "ok" and small.mismatched == 1                        # one token in 100k is inside the 2% band


def test_a_ledger_call_too_recent_to_be_in_the_logs_yet_is_not_missing():
    fresh = datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc)
    r = reconcile([led("new", ts=fresh)], aws(log_record("other", ts=NOON)), day=DAY, threshold=0.02, now=fresh + timedelta(minutes=30))
    assert r.missing_in_aws == 0
    r2 = reconcile([led("new", ts=fresh)], aws(log_record("other", ts=NOON)), day=DAY, threshold=0.02, now=fresh + timedelta(hours=5))
    assert r2.missing_in_aws == 1


def test_estimated_ledger_rows_are_not_held_to_the_exact_count_but_still_total():
    r = run([led("a", inp=100, out=20, est=True)], aws(log_record("a", inp=100, out=60)))
    assert (r.estimated_pairs, r.mismatched, r.matched) == (1, 0, 0)
    assert r.ledger_tokens == 120 and r.aws_tokens == 160


def test_other_applications_in_the_account_can_be_filtered_out_by_role():
    mine, other = log_record("a", role="arn:aws:sts::1:assumed-role/devmind/i-1"), log_record("z", role="arn:aws:sts::1:assumed-role/some-other-app/i-9", inp=9999)
    r = run([led("a")], aws(mine, other), identity_contains="assumed-role/devmind/")
    assert (r.status, r.missing_in_ledger, r.aws_tokens) == ("ok", 0, 120)
    assert run([led("a")], aws(mine, other)).missing_in_ledger == 1


def test_a_call_near_midnight_belongs_to_the_day_of_its_own_timestamp_and_is_not_counted_twice():
    late_prev = datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc)
    r = run([led("p", ts=late_prev), led("a")], aws(log_record("p", ts=late_prev), log_record("a")))
    assert r.matched == 1 and r.ledger_tokens == 120                             # yesterday's call is in the margin only


def test_no_log_records_is_no_data_not_a_zero_drift():
    r = run([led("a")], [])
    assert r.status == "no_data" and r.drift is None and "logging may be off" in r.note
    assert run([led("a")], [], unreadable=3).status != "no_data"


def test_ledger_rows_with_no_request_id_are_counted_but_cannot_be_proven():
    r = run([led(None, inp=500, out=0), led("a")], aws(log_record("a")))
    assert r.unmatchable_ledger == 1 and r.ledger_tokens == 620 and r.aws_tokens == 120


def test_one_model_has_one_name_whichever_side_reports_it():
    assert {model_key(m) for m in ("eu.anthropic.claude-sonnet-5-v1:0", "claude-sonnet-5", "arn:aws:bedrock:eu-west-2:1:inference-profile/eu.anthropic.claude-sonnet-5", "anthropic.claude-sonnet-5-v2:0")} == {"claude-sonnet-5"}
    assert model_key(None) == "unknown" and model_key("") == "unknown"


def test_the_per_model_breakdown_uses_aws_names_and_sums_both_sides():
    r = run([led("a"), led("b")], aws(log_record("a", model="m1"), log_record("b", model="m2", inp=200)))
    got = {m["model"]: (m["ledger"], m["aws"]) for m in r.by_model}
    assert got == {"m1": (120, 120), "m2": (120, 220)}
    r2 = run([led("a", model="claude-sonnet-5")], aws(log_record("a", model="eu.anthropic.claude-sonnet-5-v1:0")))
    assert [m["model"] for m in r2.by_model] == ["claude-sonnet-5"]


# ----------------------------------------------------------------------------- sources
def test_file_source_reads_jsonl_json_and_gz_and_skips_what_is_not_a_log(tmp_path):
    import asyncio

    (tmp_path / "a.jsonl").write_text("\n".join(json.dumps(log_record(f"j{i}")) for i in range(2)) + "\nnot json\n")
    (tmp_path / "b.json").write_text(json.dumps(log_record("b1")))
    with gzip.open(tmp_path / "c.json.gz", "wt") as f:
        f.write(json.dumps(log_record("g1")) + "\n" + json.dumps({"schemaType": "Other"}) + "\n")
    (tmp_path / "ignore.txt").write_text("x")
    since, until = datetime(2026, 10, 9, 23, tzinfo=timezone.utc), datetime(2026, 10, 11, 1, tzinfo=timezone.utc)
    calls, bad = asyncio.run(FileSource(str(tmp_path)).calls(since, until))
    assert sorted(c.request_id for c in calls) == ["b1", "g1", "j0", "j1"] and bad == 1
    outside, _ = asyncio.run(FileSource(str(tmp_path)).calls(since + timedelta(days=5), until + timedelta(days=5)))
    assert outside == []


class _Paginator:
    def __init__(self, pages): self.pages = pages
    def paginate(self, **kw): self.kw = kw; return iter(self.pages)


def test_cloudwatch_source_reads_events_in_the_window_through_the_paginator():
    import asyncio

    msgs = [{"message": json.dumps(log_record("c1"))}, {"message": json.dumps(log_record("c2"))}, {"message": "garbage"}]
    pag = _Paginator([{"events": msgs[:2]}, {"events": msgs[2:]}])
    client = SimpleNamespace(get_paginator=lambda name: pag if name == "filter_log_events" else None)
    since, until = NOON - MARGIN, NOON + MARGIN
    calls, bad = asyncio.run(CloudWatchSource("/aws/bedrock/invocations", "eu-west-2", client=client).calls(since, until))
    assert [c.request_id for c in calls] == ["c1", "c2"] and bad == 1
    assert pag.kw["logGroupName"] == "/aws/bedrock/invocations" and pag.kw["startTime"] == int(since.timestamp() * 1000)


def test_s3_source_finds_the_account_and_region_folders_and_reads_each_hour_but_not_the_data_folder():
    import asyncio

    key = "logs/AWSLogs/123/BedrockModelInvocationLogs/eu-west-2/2026/10/10/12/"
    objects = {
        "logs/AWSLogs/": {"CommonPrefixes": [{"Prefix": "logs/AWSLogs/123/"}]},
        "logs/AWSLogs/123/": {"CommonPrefixes": [{"Prefix": "logs/AWSLogs/123/BedrockModelInvocationLogs/"}, {"Prefix": "logs/AWSLogs/123/Other/"}]},
        "logs/AWSLogs/123/BedrockModelInvocationLogs/": {"CommonPrefixes": [{"Prefix": "logs/AWSLogs/123/BedrockModelInvocationLogs/eu-west-2/"}]},
        key: {"Contents": [{"Key": key + "x.json.gz"}, {"Key": key + "data/big.json.gz"}]},
    }
    body = gzip.compress((json.dumps(log_record("s1")) + "\n").encode())
    fetched: list[str] = []

    class Pg:
        def __init__(self, prefix): self.prefix = prefix
        def paginate(self, **kw): return iter([objects.get(kw["Prefix"], {})])

    client = SimpleNamespace(get_paginator=lambda _n: SimpleNamespace(paginate=lambda **kw: Pg(kw["Prefix"]).paginate(**kw)),
                             get_object=lambda Bucket, Key: (fetched.append(Key), {"Body": SimpleNamespace(read=lambda: body)})[1])
    calls, _ = asyncio.run(S3Source("b", "logs", client=client).calls(NOON - MARGIN, NOON + MARGIN))
    assert [c.request_id for c in calls] == ["s1"] and fetched == [key + "x.json.gz"]


def test_a_source_is_built_only_when_it_is_fully_configured():
    s = lambda **kw: SimpleNamespace(BEDROCK_RECON_SOURCE="", BEDROCK_RECON_LOG_GROUP="", BEDROCK_RECON_S3_BUCKET="", BEDROCK_RECON_S3_PREFIX="", BEDROCK_RECON_FILE_DIR="",  # noqa: E731
                                     BEDROCK_RECON_REGION="", BEDROCK_REGION="eu-west-2", AWS_REGION="x", **kw)
    assert build_recon_source(s()) is None
    assert build_recon_source(s(**{"BEDROCK_RECON_SOURCE": "cloudwatch"}) if False else SimpleNamespace(**{**s().__dict__, "BEDROCK_RECON_SOURCE": "cloudwatch"})) is None
    assert build_recon_source(SimpleNamespace(**{**s().__dict__, "BEDROCK_RECON_SOURCE": "cloudwatch", "BEDROCK_RECON_LOG_GROUP": "g"})).name == "cloudwatch"
    assert build_recon_source(SimpleNamespace(**{**s().__dict__, "BEDROCK_RECON_SOURCE": "s3", "BEDROCK_RECON_S3_BUCKET": "b"})).name == "s3"
    assert build_recon_source(SimpleNamespace(**{**s().__dict__, "BEDROCK_RECON_SOURCE": "file", "BEDROCK_RECON_FILE_DIR": "/x"})).name == "file"


# ----------------------------------------------------------------------------- the service
class _Repo:
    def __init__(self, ledger=None): self.ledger, self.saved, self.done = ledger or [], [], set()
    async def bedrock_ledger(self, since, until): return [r for r in self.ledger if since <= r["ts"] < until]
    async def needs_run(self, day): return day not in self.done
    async def save_run(self, run, diffs): self.saved.append((run, diffs)); self.done.add(run["day"]) if run["status"] != "error" else None


class _Source:
    name = "file"
    def __init__(self, calls=None, fail=False): self._calls, self._fail = calls or [], fail
    async def calls(self, since, until):
        if self._fail: raise RuntimeError("AccessDenied: logs:FilterLogEvents")
        return [c for c in self._calls if since <= c.ts < until], 0


async def test_a_run_is_recorded_with_its_differences_and_a_drift_is_logged(caplog):
    repo = _Repo([led("a", inp=1000, out=0)])
    rec = BedrockReconciler(repo, _Source(aws(log_record("a", inp=500, out=0))))
    out = await rec.run_day(DAY)
    run, diffs = repo.saved[0]
    assert out["status"] == "drift" and run["status"] == "drift" and run["day"] == DAY and len(diffs) == 1
    assert "TOKEN LEDGER DRIFT" in caplog.text


async def test_a_failing_source_is_recorded_as_an_error_not_raised():
    repo = _Repo()
    out = await BedrockReconciler(repo, _Source(fail=True)).run_day(DAY)
    assert out["status"] == "error" and "AccessDenied" in repo.saved[0][0]["error"]
    assert DAY not in repo.done                                                  # an errored day is retried by the next tick


async def test_the_scheduler_catches_up_completed_days_without_redoing_finished_ones():
    class Redis:
        def __init__(self): self.keys = {}
        async def set(self, k, v, nx=False, ex=None):
            if nx and k in self.keys: return None
            self.keys[k] = v; return True
        async def delete(self, k): self.keys.pop(k, None)

    repo, redis = _Repo(), Redis()
    repo.done.add(date(2026, 10, 11))
    rec = BedrockReconciler(repo, _Source([]), redis=redis)
    ran = await rec.run_missing(3, now=datetime(2026, 10, 12, 4, tzinfo=timezone.utc))
    assert [r["day"] for r in ran] == ["2026-10-09", "2026-10-10"]               # oldest first; 11th already done; today is not complete
    redis.keys["sdlc:recon:lock"] = "other-replica"
    assert await rec.run_missing(3, now=datetime(2026, 10, 12, 4, tzinfo=timezone.utc)) == []


async def test_a_day_with_no_log_yet_is_tried_again_later_but_not_every_tick():
    import asyncpg

    from app.repos.pg import Database
    from app.repos.recon_repo import ReconRepo

    if not ADMIN:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"rec_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(Path(__file__).resolve().parents[3] / "infra" / "migrations")
        repo = ReconRepo(db)
        assert await repo.needs_run(DAY)                                          # never run
        await BedrockReconciler(repo, _Source([])).run_day(DAY)                   # no log records: no_data
        assert not await repo.needs_run(DAY)                                      # just tried: do not hammer the log source every 15 minutes
        await db.pool.execute("UPDATE usage_reconciliation_runs SET finished_at = now() - interval '7 hours'")
        assert await repo.needs_run(DAY)                                          # a later tick tries again (logs can arrive late)
        await BedrockReconciler(repo, _Source(fail=True)).run_day(DAY)
        assert not await repo.needs_run(DAY)
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_an_unconfigured_scheduler_does_nothing():
    sch = ReconcileScheduler(BedrockReconciler(_Repo(), None))
    sch.start()
    assert sch._task is None
    await sch.stop()


# ----------------------------------------------------------------------------- real Postgres
ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")


@needs_pg
async def test_runs_are_stored_replaced_per_day_and_read_back_with_their_differences():
    import asyncpg

    from app.repos.pg import Database
    from app.repos.recon_repo import ReconRepo

    name = f"rec_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(Path(__file__).resolve().parents[3] / "infra" / "migrations")
        for rid, pid, tok in (("a", "p1", 1000), ("b", "p1", 50)):
            await db.pool.execute("INSERT INTO llm_usage_events (id,request_id,provider,model,project_id,user_id,activity,input_tokens,output_tokens,status,provider_request_id,ts) "
                                  "VALUES ($1,$1,'bedrock','m',$2,'u','stage.generate',$3,0,'ok',$1,$4)", rid, pid, tok, NOON)
        await db.pool.execute("INSERT INTO llm_usage_events (id,request_id,provider,model,input_tokens,status,activity,ts) VALUES ('m1','m1','mock','m',9,'ok','other',$1)", NOON)
        repo = ReconRepo(db)
        rec = BedrockReconciler(repo, _Source(aws(log_record("a", inp=800, out=0), log_record("c", inp=70, out=0))))
        out = await rec.run_day(DAY, "test")
        assert out["status"] == "drift"
        got = await repo.runs(5)
        assert len(got) == 1 and (got[0]["matched"], got[0]["mismatched"], got[0]["missing_in_ledger"], got[0]["missing_in_aws"]) == (0, 1, 1, 1)
        assert got[0]["ledger_tokens"] == 1050 and got[0]["aws_tokens"] == 870          # the mock call is not a Bedrock call
        d = await repo.diffs(got[0]["id"])
        assert [(x["kind"], x["delta"]) for x in d] == [("mismatch", 200), ("missing_in_ledger", -70), ("missing_in_aws", 50)]
        assert not await repo.needs_run(DAY)
        await BedrockReconciler(repo, _Source(aws(log_record("a", inp=1000, out=0), log_record("b", inp=50, out=0)))).run_day(DAY, "rerun")
        again = await repo.runs(5)
        assert len(again) == 1 and again[0]["status"] == "ok" and again[0]["triggered_by"] == "rerun"
        assert await repo.diffs(again[0]["id"]) == []
        assert await db.pool.fetchval("SELECT count(*) FROM usage_reconciliation_diffs") == 0     # the replaced run's differences are gone with it
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
