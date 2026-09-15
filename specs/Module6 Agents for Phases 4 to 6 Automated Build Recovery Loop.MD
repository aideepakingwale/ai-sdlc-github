Module 6: Agents for Phases 4 to 6 & Automated Build Recovery Loop

Phase 04: QA Lead Agent

Logic (Fig 5.1): Context = LLD + OpenAPI. Generate Test Strategy. Loop through stories to call MCP Xray.createTestIssue(). Generate k6 performance scripts (JavaScript) and Postman collections. Generate Requirements Traceability Matrix (RTM). Trigger QA human gate.

Phase 05: DevOps Engineer Agent

Logic (Fig 6.1): Context = CDK + Test Scripts. Generate GitHub Actions YAML (7 stages: checkout, lint, build, test, snyk-scan, inspector-scan, deploy). Generate Dockerfiles and Grafana JSON. Call MCP GitHub.commitPipelineConfig(). Perform a Dry-Run. Trigger DevOps human gate.

Phase 06: Developer Agent

Logic: Generate App code matching LLD exactly. Generate unit tests (Jest/Pytest target >=80%). Call MCP GitHub.createBranch() and commitCode(). This push triggers the GitHub Actions pipeline.

The Automated Build Recovery Loop (Fig 7.1 & 7.2)

Implemented as a state machine in BuildMonitor service tracking state in DynamoDB (BuildRecoveryTracker).

State PIPELINE_RUNNING: Polling GitHub API every 30s (GitHub.pollRunStatus).

On SUCCESS: Exit loop. Call MCP GitHub.createPullRequest().

On FAILURE (State ANALYSING_FAILURE):

Increment iteration_count in DynamoDB.

If iteration_count > 5: Change state to ESCALATED. Alert Human Developer. STOP.

If iteration_count <= 5:

Call MCP GitHub.fetchBuildLogs(run_id).

Call AmazonQ.analyseFailure(logs). Extract root cause (e.g., "snyk:CVE", "type-error").

Call GHCopilot.generateFix(root_cause, files).

Call MCP GitHub.commitFix(diff, msg="fix(ai): iter-N").

Change state back to PIPELINE_RUNNING (wait for the new webhook/poll).

Root Cause Classifications to handle:

TypeScript type errors -> Copilot fixes annotations.

Failing assertions -> Copilot fixes implementation or mock.

Snyk SAST/SCA -> AmazonQ patches package.json semver.

Snyk IaC -> Copilot patches CDK property.

AWS Inspector -> AmazonQ upgrades Dockerfile FROM base image.