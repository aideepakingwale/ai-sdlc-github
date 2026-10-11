"""Reconcile the token ledger with Bedrock's own invocation logs (docs/bedrock-reconciliation.md).

The ledger is the system of record, but it is only as good as what the gateway saw. Bedrock model invocation logging
records every call AWS served, with the provider's request id, so a nightly comparison catches calls the ledger missed
(a crashed worker, a stream that died before reporting), calls it counted that AWS did not, and token counts that differ.

Matching key: the request id. The gateway keeps Bedrock's `x-amzn-requestid` as `provider_request_id`; the log's
`requestId` is the same id.

How the log counts input is not something to assume (is `inputTokenCount` the uncached part, or everything?), so the
comparison tries both readings per request and uses the one that explains more requests, and says which it used.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger("bedrock-reconcile")

MARGIN = timedelta(hours=1)           # records near midnight are fetched from both sides and assigned to a day by one timestamp
GRACE = timedelta(hours=2)            # a ledger call newer than this may simply not be in the logs yet
MAX_DIFFS = 200
CONVENTION_EXCLUDES = "input_excludes_cache"
CONVENTION_INCLUDES = "input_includes_cache"
CONVENTION_EITHER = "indeterminate"


# --------------------------------------------------------------------------------------------- the AWS side
@dataclass(frozen=True)
class AwsCall:
    request_id: str
    ts: datetime
    model_id: str
    operation: str
    input_tokens: int            # exactly as the log reports it (see the convention)
    cache_read: int
    cache_write: int
    output_tokens: int
    identity: str = ""
    failed: bool = False         # the invocation errored; nothing billed


def _int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return max(0, int(v))
    except (TypeError, ValueError, OverflowError):      # OverflowError: a log value such as 1e999 parses to infinity
        return None


def _ts(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _usage_from_body(body: Any) -> tuple[int, int, int, int] | None:
    """(input, cache_read, cache_write, output) from a logged Anthropic response body: a message, or a list of stream events."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return None
    inp = cr = cw = out = None
    events = body if isinstance(body, list) else [body]
    for ev in events:
        if not isinstance(ev, dict):
            continue
        u = ev.get("usage") or (ev.get("message") or {}).get("usage")
        if not isinstance(u, dict):
            continue
        inp = _int(u.get("input_tokens")) if _int(u.get("input_tokens")) is not None else inp
        cr = _int(u.get("cache_read_input_tokens")) if _int(u.get("cache_read_input_tokens")) is not None else cr
        cw = _int(u.get("cache_creation_input_tokens")) if _int(u.get("cache_creation_input_tokens")) is not None else cw
        out = _int(u.get("output_tokens")) if _int(u.get("output_tokens")) is not None else out
    if inp is None and out is None:
        return None
    return (inp or 0, cr or 0, cw or 0, out or 0)


def parse_invocation_record(raw: dict[str, Any]) -> AwsCall | None:
    """One Bedrock invocation log record -> the call, or None when it is not a usable invocation record.

    Token counts are read from the log's own fields (`input.inputTokenCount`, `input.cacheReadInputTokenCount`,
    `input.cacheWriteInputTokenCount`, `output.outputTokenCount`) and, if a record lacks them, from the usage block of
    the logged response body. Only the counts are kept: prompts and answers never leave this function."""
    rid = raw.get("requestId")
    ts = _ts(raw.get("timestamp"))
    if not isinstance(rid, str) or not rid or ts is None:
        return None
    inp, out = raw.get("input"), raw.get("output")
    inp, out = (inp if isinstance(inp, dict) else {}), (out if isinstance(out, dict) else {})
    counts = [_int(inp.get("inputTokenCount")), _int(inp.get("cacheReadInputTokenCount")), _int(inp.get("cacheWriteInputTokenCount")), _int(out.get("outputTokenCount"))]
    failed = bool(raw.get("errorCode"))
    if counts[0] is None and counts[3] is None:
        body = _usage_from_body(out.get("outputBodyJson"))
        if body is None and not failed:
            return None                      # a record we cannot read tokens from: reported as unreadable by the caller
        counts = list(body) if body else [0, 0, 0, 0]
    ident = raw.get("identity") or {}
    return AwsCall(
        request_id=rid, ts=ts, model_id=str(raw.get("modelId") or ""), operation=str(raw.get("operation") or ""),
        input_tokens=counts[0] or 0, cache_read=counts[1] or 0, cache_write=counts[2] or 0, output_tokens=counts[3] or 0,
        identity=str(ident.get("arn") or "") if isinstance(ident, dict) else str(ident), failed=failed,
    )


class LogSource(Protocol):
    name: str

    async def calls(self, since: datetime, until: datetime) -> tuple[list[AwsCall], int]:
        """(parsed calls in [since, until), number of records that could not be read)"""
        ...


def _parse_lines(lines: Any, since: datetime, until: datetime, out: list[AwsCall]) -> int:
    unreadable = 0
    for line in lines:
        line = line.strip() if isinstance(line, str) else line.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        if not isinstance(raw, dict) or raw.get("schemaType", "ModelInvocationLog") != "ModelInvocationLog":
            continue
        try:
            call = parse_invocation_record(raw)
        except Exception:  # noqa: BLE001 — one malformed record must not abort the day: it is counted as unreadable
            call = None
        if call is None:
            unreadable += 1
        elif since <= call.ts < until:
            out.append(call)
    return unreadable


class FileSource:
    """A directory of exported logs (.json, .jsonl, .gz), for offline reconciliation and tests."""
    name = "file"

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)

    async def calls(self, since: datetime, until: datetime) -> tuple[list[AwsCall], int]:
        def run() -> tuple[list[AwsCall], int]:
            out: list[AwsCall] = []
            bad = 0
            for p in sorted(self._dir.rglob("*")):
                if not p.is_file() or p.suffix not in (".json", ".jsonl", ".gz"):
                    continue
                opener: Callable[..., Any] = gzip.open if p.suffix == ".gz" else open
                with opener(p, "rb") as f:
                    bad += _parse_lines(io.TextIOWrapper(f, encoding="utf-8"), since, until, out)
            return out, bad
        return await asyncio.to_thread(run)


class CloudWatchSource:
    """Bedrock invocation logging delivered to a CloudWatch Logs group."""
    name = "cloudwatch"

    def __init__(self, log_group: str, region: str | None, max_events: int = 2_000_000, client: Any = None) -> None:
        self._group, self._region, self._max, self._client = log_group, region, max_events, client
        self.capped = False          # set when a read stopped at the cap: the day is then incomplete and must not be reported as a clean comparison

    async def calls(self, since: datetime, until: datetime) -> tuple[list[AwsCall], int]:
        def run() -> tuple[list[AwsCall], int]:
            client = self._client
            if client is None:
                import boto3
                client = boto3.client("logs", region_name=self._region)      # the default credential chain (role), never the local dummy keys
            out: list[AwsCall] = []
            bad = seen = 0
            for page in client.get_paginator("filter_log_events").paginate(
                    logGroupName=self._group, startTime=int(since.timestamp() * 1000), endTime=int(until.timestamp() * 1000)):
                events = page.get("events", [])
                seen += len(events)
                bad += _parse_lines((e.get("message", "") for e in events), since, until, out)
                if seen >= self._max:
                    log.warning("CloudWatch log read stopped at %d events; the day is incomplete", seen)
                    self.capped = True
                    break
            return out, bad
        return await asyncio.to_thread(run)


class S3Source:
    """Bedrock invocation logging delivered to S3: `<prefix>/AWSLogs/<account>/BedrockModelInvocationLogs/<region>/<yyyy>/<mm>/<dd>/<hh>/*.json.gz`."""
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "", region: str | None = None, client: Any = None) -> None:
        self._bucket, self._prefix, self._region, self._client = bucket, prefix.strip("/"), region, client

    async def calls(self, since: datetime, until: datetime) -> tuple[list[AwsCall], int]:
        def run() -> tuple[list[AwsCall], int]:
            client = self._client
            if client is None:
                import boto3
                client = boto3.client("s3", region_name=self._region)
            base = f"{self._prefix}/AWSLogs/" if self._prefix else "AWSLogs/"

            def subdirs(prefix: str) -> list[str]:
                out: list[str] = []
                for page in client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix, Delimiter="/"):
                    out += [c["Prefix"] for c in page.get("CommonPrefixes", [])]
                return out

            roots: list[str] = []
            for account in subdirs(base):
                for kind in subdirs(account):
                    if kind.rstrip("/").endswith("BedrockModelInvocationLogs"):
                        roots += subdirs(kind)                   # one per region
            hours: list[str] = []
            t = since.replace(minute=0, second=0, microsecond=0)
            while t < until:
                hours.append(t.strftime("%Y/%m/%d/%H/"))
                t += timedelta(hours=1)
            out: list[AwsCall] = []
            bad = 0
            for root in roots:
                for h in hours:
                    for page in client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=root + h):
                        for obj in page.get("Contents", []):
                            key = obj["Key"]
                            rel = key[len(root + h):]
                            if rel.startswith("data/") or "/data/" in rel:     # large request/response bodies are stored separately; the counts are in the log file
                                continue
                            body = client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
                            if key.endswith(".gz"):
                                body = gzip.decompress(body)
                            bad += _parse_lines(body.decode("utf-8", "replace").splitlines(), since, until, out)
            return out, bad
        return await asyncio.to_thread(run)


# --------------------------------------------------------------------------------------------- the comparison
@dataclass
class ReconResult:
    status: str
    convention: str
    ledger_tokens: int
    aws_tokens: int
    drift: float | None
    matched: int
    mismatched: int
    missing_in_ledger: int
    missing_in_aws: int
    unmatchable_ledger: int
    estimated_pairs: int
    unreadable: int
    by_model: list[dict[str, Any]] = field(default_factory=list)
    diffs: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


def model_key(model: str | None) -> str:
    """One name for a model whichever side reports it: an inference-profile ARN, `eu.anthropic.claude-x-v1:0` and `claude-x` are the same model."""
    m = (model or "").strip().lower().rsplit("/", 1)[-1]
    m = re.sub(r"^(eu|us|apac|global|us-gov)\.", "", m)
    m = m.removeprefix("anthropic.")
    return re.sub(r"-v\d+(:\d+)?$", "", m) or "unknown"


def _ledger_total(r: dict[str, Any]) -> int:
    return int(r["input_tokens"]) + int(r["cache_read_tokens"]) + int(r["cache_write_tokens"]) + int(r["output_tokens"])


def _aws_total(c: AwsCall, convention: str) -> int:
    inp = c.input_tokens if convention == CONVENTION_INCLUDES else c.input_tokens + c.cache_read + c.cache_write
    return inp + c.output_tokens


def reconcile(
    ledger: list[dict[str, Any]], aws: list[AwsCall], *, day: date, threshold: float, now: datetime | None = None,
    unreadable: int = 0, identity_contains: str = "",
) -> ReconResult:
    """Compare one UTC day. `ledger` rows are Bedrock ledger rows (a margin either side of the day is fine);
    `aws` is every logged call in the same window. Pure: no I/O."""
    now = now or datetime.now(timezone.utc)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    if identity_contains:
        aws = [c for c in aws if identity_contains in c.identity]
    aws = [c for c in aws if not c.failed or c.input_tokens or c.output_tokens]
    by_id_aws: dict[str, AwsCall] = {}
    for c in aws:
        by_id_aws.setdefault(c.request_id, c)           # a request id appears once; a duplicate delivery is ignored
    by_id_ledger: dict[str, list[dict[str, Any]]] = {}
    unmatchable = 0
    for r in ledger:
        rid = r.get("provider_request_id")
        if rid:
            by_id_ledger.setdefault(rid, []).append(r)
        elif _ledger_total(r) > 0 and start <= r["ts"] < end:
            unmatchable += 1

    pairs = [(by_id_aws[rid], rows) for rid, rows in by_id_ledger.items() if rid in by_id_aws]
    # Which reading of the log's input count explains more requests? Estimated ledger rows cannot vote.
    votes = {CONVENTION_EXCLUDES: 0, CONVENTION_INCLUDES: 0}
    for c, rows in pairs:
        if any(r.get("estimated") for r in rows):
            continue
        total = sum(_ledger_total(r) for r in rows)
        for conv in votes:
            votes[conv] += int(_aws_total(c, conv) == total)
    if votes[CONVENTION_EXCLUDES] == votes[CONVENTION_INCLUDES]:
        convention = CONVENTION_EITHER
        use = CONVENTION_EXCLUDES
    else:
        use = max(votes, key=lambda k: votes[k])
        convention = use

    in_day = lambda ts: start <= ts < end  # noqa: E731
    matched = mismatched = estimated_pairs = 0
    ledger_tokens = aws_tokens = 0
    models: dict[str, dict[str, int]] = {}
    diffs: list[dict[str, Any]] = []

    def bump(model: str, l: int, a: int) -> None:
        m = models.setdefault(model_key(model), {"ledger": 0, "aws": 0, "calls": 0})
        m["ledger"] += l
        m["aws"] += a
        m["calls"] += 1

    for c, rows in pairs:
        if not in_day(c.ts):
            continue
        l, a = sum(_ledger_total(r) for r in rows), _aws_total(c, use)
        ledger_tokens += l
        aws_tokens += a
        bump(c.model_id, l, a)
        if any(r.get("estimated") for r in rows):
            estimated_pairs += 1
            continue
        if l == a:
            matched += 1
        else:
            mismatched += 1
            diffs.append({"kind": "mismatch", "provider_request_id": c.request_id, "model": c.model_id, "ts": c.ts, "ledger_tokens": l, "aws_tokens": a, "delta": l - a,
                          "project_id": rows[0].get("project_id"), "activity": rows[0].get("activity")})
    missing_in_ledger = 0
    for rid, c in by_id_aws.items():
        if rid in by_id_ledger or not in_day(c.ts):
            continue
        a = _aws_total(c, use)
        if a <= 0:
            continue
        missing_in_ledger += 1
        aws_tokens += a
        bump(c.model_id, 0, a)
        diffs.append({"kind": "missing_in_ledger", "provider_request_id": rid, "model": c.model_id, "ts": c.ts, "ledger_tokens": 0, "aws_tokens": a, "delta": -a, "project_id": None, "activity": None})
    missing_in_aws = 0
    for rid, rows in by_id_ledger.items():
        if rid in by_id_aws:
            continue
        ts = min(r["ts"] for r in rows)
        l = sum(_ledger_total(r) for r in rows)
        if not in_day(ts) or l <= 0 or now - ts < GRACE:
            continue
        missing_in_aws += 1
        ledger_tokens += l
        bump(rows[0].get("model") or "", l, 0)
        diffs.append({"kind": "missing_in_aws", "provider_request_id": rid, "model": rows[0].get("model"), "ts": ts, "ledger_tokens": l, "aws_tokens": 0, "delta": l,
                      "project_id": rows[0].get("project_id"), "activity": rows[0].get("activity")})
    # Ledger rows with no request id still count toward the ledger's day total: they are exactly what cannot be proven.
    for r in ledger:
        if not r.get("provider_request_id") and in_day(r["ts"]):
            ledger_tokens += _ledger_total(r)

    drift = max(-9999.0, min(9999.0, round((ledger_tokens - aws_tokens) / aws_tokens, 4))) if aws_tokens else None      # fits the NUMERIC(8,4) column
    diffs.sort(key=lambda d: abs(d["delta"]), reverse=True)
    if not aws and not unreadable:
        status, note = "no_data", "No Bedrock invocation log records were found for this day: logging may be off, the wrong log group, or the day has not been delivered yet."
    elif drift is not None and abs(drift) > threshold:
        status, note = "drift", "The ledger and AWS disagree by more than the threshold; see the differences."
    else:
        status, note = "ok", ""
    if unreadable:
        note = (note + " " if note else "") + f"{unreadable} log records could not be read for tokens."
    if convention == CONVENTION_EITHER and matched:
        note = (note + " " if note else "") + "The log's input count could not be told apart from the ledger's reading (no cached input in the matched calls)."
    return ReconResult(
        status=status, convention=convention, ledger_tokens=ledger_tokens, aws_tokens=aws_tokens, drift=drift, matched=matched, mismatched=mismatched,
        missing_in_ledger=missing_in_ledger, missing_in_aws=missing_in_aws, unmatchable_ledger=unmatchable, estimated_pairs=estimated_pairs, unreadable=unreadable,
        by_model=[{"model": k, **v, "drift": round((v["ledger"] - v["aws"]) / v["aws"], 4) if v["aws"] else None} for k, v in sorted(models.items(), key=lambda kv: -kv[1]["aws"])],
        diffs=diffs[:MAX_DIFFS], note=note,
    )


# --------------------------------------------------------------------------------------------- the service
class BedrockReconciler:
    def __init__(self, repo: Any, source: LogSource | None, *, threshold: float = 0.02, identity_contains: str = "", redis: Any = None) -> None:
        self._repo, self._source, self._threshold, self._identity, self._redis = repo, source, threshold, identity_contains, redis

    @property
    def configured(self) -> bool:
        return self._source is not None

    @property
    def source_name(self) -> str:
        return self._source.name if self._source else ""

    async def run_day(self, day: date, triggered_by: str = "schedule") -> dict[str, Any]:
        if self._source is None:
            raise RuntimeError("Reconciliation is not configured")
        started = datetime.now(timezone.utc)
        since = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - MARGIN
        until = since + timedelta(days=1) + 2 * MARGIN
        run_id = str(uuid.uuid4())
        try:
            self._source.capped = False
            aws, unreadable = await self._source.calls(since, until)
            if getattr(self._source, "capped", False):
                raise RuntimeError("the log read stopped at its event cap, so this day is incomplete: raise the cap, or deliver the logs to S3")
            ledger = await self._repo.bedrock_ledger(since, until)
            res = reconcile(ledger, aws, day=day, threshold=self._threshold, unreadable=unreadable, identity_contains=self._identity)
            await self._repo.save_run({
                "id": run_id, "day": day, "source": self.source_name, "triggered_by": triggered_by, "started_at": started, "status": res.status, "threshold": self._threshold,
                "convention": res.convention, "ledger_tokens": res.ledger_tokens, "aws_tokens": res.aws_tokens, "drift": res.drift, "matched": res.matched, "mismatched": res.mismatched,
                "missing_in_ledger": res.missing_in_ledger, "missing_in_aws": res.missing_in_aws, "unmatchable_ledger": res.unmatchable_ledger, "estimated_pairs": res.estimated_pairs,
                "by_model": res.by_model, "note": res.note or None}, res.diffs)
        except Exception as err:  # noqa: BLE001 — a failed night is recorded, not raised into the scheduler
            log.error("bedrock reconciliation for %s failed", day, exc_info=True)
            try:
                await self._repo.save_run({"id": run_id, "day": day, "source": self.source_name, "triggered_by": triggered_by, "started_at": started, "status": "error",
                                           "threshold": self._threshold, "error": str(err)[:500]}, [])
            except Exception:  # noqa: BLE001
                log.error("could not record the failed reconciliation for %s", day, exc_info=True)
            return {"day": day.isoformat(), "status": "error", "error": str(err)[:500]}
        if res.status == "drift":
            log.warning("TOKEN LEDGER DRIFT %s: ledger=%d aws=%d drift=%.2f%% (threshold %.1f%%), %d mismatched, %d missing in ledger, %d missing in AWS",
                        day, res.ledger_tokens, res.aws_tokens, (res.drift or 0) * 100, self._threshold * 100, res.mismatched, res.missing_in_ledger, res.missing_in_aws)
        return {"day": day.isoformat(), "status": res.status, "drift": res.drift}

    async def run_missing(self, days_back: int = 3, now: datetime | None = None) -> list[dict[str, Any]]:
        """Reconcile every completed UTC day in the last `days_back` that has no run yet (oldest first). One replica at a time."""
        now = now or datetime.now(timezone.utc)
        locked = False
        token = uuid.uuid4().hex
        if self._redis is not None:
            try:
                if not await self._redis.set("sdlc:recon:lock", token, nx=True, ex=1800):
                    return []
                locked = True
            except Exception:  # noqa: BLE001 — without Redis two replicas may both run; a day's result is replaced atomically, so that is harmless
                log.warning("reconciliation lock unavailable; running without it", exc_info=True)
        out: list[dict[str, Any]] = []
        try:
            today = now.date()
            for back in range(days_back, 0, -1):
                d = today - timedelta(days=back)
                if not await self._repo.needs_run(d):
                    continue
                out.append(await self.run_day(d, "schedule"))
        finally:
            if locked:
                try:    # only release a lock that is still ours (a run longer than the TTL must not free another replica's)
                    held = await self._redis.get("sdlc:recon:lock")
                    if (held.decode() if isinstance(held, (bytes, bytearray)) else held) == token:
                        await self._redis.delete("sdlc:recon:lock")
                except Exception:  # noqa: BLE001
                    pass
        return out


class ReconcileScheduler:
    """Checks every 15 minutes; after `hour_utc` it reconciles the days that are complete and not yet done."""

    def __init__(self, reconciler: BedrockReconciler, hour_utc: int = 3) -> None:
        self._r, self._hour = reconciler, hour_utc
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None and self._r.configured:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="bedrock-reconcile")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                if datetime.now(timezone.utc).hour >= self._hour:
                    await self._r.run_missing()
            except Exception:  # noqa: BLE001
                log.error("reconcile scheduler tick failed", exc_info=True)
            await asyncio.sleep(900)



def build_recon_source(settings: Any) -> LogSource | None:
    """The configured log source, or None (reconciliation off) when none is set or its required setting is missing."""
    kind = getattr(settings, "BEDROCK_RECON_SOURCE", "")
    region = settings.BEDROCK_RECON_REGION or settings.BEDROCK_REGION or settings.AWS_REGION
    if kind == "cloudwatch" and settings.BEDROCK_RECON_LOG_GROUP:
        return CloudWatchSource(settings.BEDROCK_RECON_LOG_GROUP, region)
    if kind == "s3" and settings.BEDROCK_RECON_S3_BUCKET:
        return S3Source(settings.BEDROCK_RECON_S3_BUCKET, settings.BEDROCK_RECON_S3_PREFIX, region)
    if kind == "file" and settings.BEDROCK_RECON_FILE_DIR:
        return FileSource(settings.BEDROCK_RECON_FILE_DIR)
    if kind:
        log.warning("BEDROCK_RECON_SOURCE=%s is set but its location setting is empty: reconciliation is off", kind)
    return None
