Master Specification: Enterprise AI-Powered SDLC Automation Platform

1. Executive Summary

This project is an end-to-end, fully autonomous Agile Software Development Lifecycle (SDLC) platform. It orchestrates specialized AI agents across six distinct phases—from initial requirement ingestion to deployed production code. The platform strictly enforces a Human-in-the-Loop (HITL) methodology: no phase can advance without explicit validation and sign-off from a human domain expert via a secure UI gate.

2. Core Design Principles

Multi-Agent Orchestration: A stateful LangGraph pipeline orchestrates workflows. A Leader Agent reasons about intent and delegates tasks to specialized sub-agents.

Tool Invocation via MCP: Agents do not have hardcoded DB/API coupling. All external system interactions (Jira, Confluence, GitHub, AWS) are invoked via Model Context Protocol (MCP) servers.

LLM-Driven Reasoning & Router: Intelligent routing using a circuit-breaker pattern. Provider hierarchy: Groq (preferred/free-tier) → Gemini Flash (fallback) → Grok. Amazon Q and GitHub Copilot are utilized for specific architectural and coding tasks.

Intelligent Context Window: As the SDLC progresses, approved artifacts from Phase N are compressed and injected into Phase N+1 to maintain full project context without token overflow.

3. High-Level Architecture

Frontend UI (React/Next.js): A chat interface acts as the primary entry point, coupled with a Role-Based Gate Dashboard for human reviewers.

Orchestration Hub (Node.js/TS): The brain of the system running on AWS ECS Fargate. It houses the LangGraph pipeline, Prompt Engine, Context Manager, and Phase Router.

Persistence Layer:

Aurora RDS (Postgres): Orchestration state and context storage.

DynamoDB: Fast transient state for phase tracking and HITL gate locks.

ElastiCache (Redis): Caching tool results (24h TTL) and rate limiting.

S3: Immutable parquet audit logs of every AI generation.

4. The 6-Phase Pipeline

Phase 01 (Product Owner): Ingests requirements; outputs structured Jira Epics, User Stories (Gherkin ACs), and Confluence PRDs.

Phase 02 (Solution Architect): Generates C4 Models (Structurizr DSL), Cloudcraft AWS topology, and Architecture Decision Records (ADRs).

Phase 03 (Technical Architect): Produces LLDs, PlantUML diagrams, OpenAPI 3.0 specs, DBML schemas, and AWS CDK IaC.

Phase 04 (QA Lead): Creates Test Strategies, Xray Test Cases, Postman collections, and k6 performance scripts.

Phase 05 (DevOps Engineer): Generates GitHub Actions pipelines, Dockerfiles, and Grafana monitoring dashboards.

Phase 06 (Developer): Writes application code and unit tests. Triggers the CI/CD pipeline.

5. Automated Build Recovery Loop

If the CI/CD pipeline fails in Phase 06, an automated recovery state machine takes over:

The AI fetches GitHub Actions build logs.

It analyzes the root cause (e.g., compile error, Snyk CVE, test failure).

It generates a targeted code fix and auto-commits the patch.

Limit: This loops up to a maximum of 5 iterations. If it fails on the 5th try, the system stops and escalates to a human developer.

6. Specification Module Map

When generating code for this platform, refer to the following detailed specification files:

01_architecture_and_db_schemas.md: DB schemas and container layout.

02_api_routes_and_core_logic.md: Backend REST endpoints and guardrails.

03_langgraph_and_llm_router.md: Graph state definition and LLM failover logic.

04_mcp_tools.md: Standardized tool calls (Jira, GitHub, Confluence).

05_phase_agents_1_to_3.md: Agent logic for PO, SA, and TA.

06_phase_agents_4_to_6_and_build_loop.md: Agent logic for QA, DevOps, Dev, and the Build Loop.

07_ui_chat_interface.md: React/Next.js frontend layout and HITL dashboard.