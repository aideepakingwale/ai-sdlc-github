Module 4: MCP Tool Connectors

Agents must NOT call DBs/APIs directly. They must use the Model Context Protocol (MCP) servers defined in tool-connector-service.

1. Jira MCP Server

createEpic(title, description, priority) -> Returns { epic_id, epic_key }

createStory(epic_id, story_text, gherkin_criteria) -> Returns { story_id, story_key }

createXrayTest(jira_story_key, steps, expected_result) -> Returns { xray_test_key }

2. Confluence MCP Server

publishPRD(template_id, content, jira_links) -> Returns { page_id, url }

publishHLDPage(template_id, hld_content, embed_structurizr, adr_links)

publishLLDPage(service_name, plantuml_macros, openapi_embed, erd_link)

3. GitHub MCP Server

commitDiagrams(structurizr_dsl, drawio_xml, cloudcraft_json, path) -> Triggers Actions

commitLLDArtefacts(plantuml_files, openapi_yaml, dbml, cdk_stacks)

commitPipelineConfig(workflow_yaml, dockerfiles, env_configs)

fetchBuildLogs(run_id, failed_job_ids) -> Returns { raw_log_text, failed_step }

commitFix(fix_diff, message)

createPullRequest(branch, title, body, checklist)

4. API & Validation Tools

AmazonQ.generateCloudcraftJSON(hld_narrative)

Spectral.lintOpenAPI(openapi_yaml) -> Returns PASS or FAIL with violation details.