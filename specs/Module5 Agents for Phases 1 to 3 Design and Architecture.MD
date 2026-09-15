Module 5: Agents for Phases 1 to 3 (Design & Architecture)

Phase 01: Product Owner Agent

Role: Product Owner. Tools: MS Copilot + GitHub Copilot MCPs.

Logic (Fig 2.1):

Receive requirements. Execute buildPhasePrompt(reqs, persona, schema).

Generate JSON containing Epics, User Stories (As/I want/So that), Gherkin criteria, Fibonacci points.

Orchestrator maps JSON to MCP createEpic and createStory calls.

Publish to Confluence via publishPRD.

Trigger GateController.createReview(reviewer='PO'). Wait for human.

Phase 02: Solution Architect Agent

Role: Solution Architect. Tools: Amazon Q.

Logic (Fig 3.1):

Context injected: Phase 1 Jira Epics + NFRs + AWS allowlist.

Generate Structurizr DSL, ADRs, and HLD narrative.

Call MCP AmazonQ.generateCloudcraftJSON(hld).

Call MCP GitHub.commitDiagrams() to trigger rendering.

Call MCP Confluence.publishHLDPage().

Trigger GateController.notifyReview(reviewer='SA'). Wait for human.

Phase 03: Technical Architect Agent

Role: Tech Architect. Tools: Amazon Q + GitHub Copilot.

Logic (Fig 4.1):

Context injected: Phase 2 HLD & ADRs.

Generate PlantUML class/sequence diagrams, OpenAPI 3.0 YAML, DBML schemas, and AWS CDK.

Mandatory Step: Run MCP Spectral.lintOpenAPI(). If FAIL, agent must auto-correct the YAML and retry.

Call MCP GitHub.commitLLDArtefacts().

Publish to SwaggerHub and Confluence LLD pages.

Trigger GateController.notifyReview(reviewer='TA'). Wait for human.