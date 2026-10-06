# Project isolation

Everything a project owns — its stages, artefacts, attachments, templates, curated knowledge, plan drafts, retrieval
corpus, live generation output and the place it publishes to — belongs to that project alone.

| Layer | How it is kept apart |
|---|---|
| Database | Every read and write filters by `project_id`; lookups by id (artefact, attachment, template, notification) also check the id belongs to the project in the URL. |
| Plans | A plan may reference only its own project's artefacts, attachments and templates (platform templates are shared). A foreign id is refused, never stored. Prompt assembly drops foreign ids again as a second guard. |
| Retrieval (RAG) | Documents are scoped `global` (enterprise standards) or to one project id; a query sees `global` + its own project. Deleting a project deletes its corpus and its templates. |
| Caches / locks | Every Redis key carries the project id and stage. |
| Live output (browser) | Each run is stored under `project:stage`; a run in one project or stage never shows in another. Signing out drops all runs. Switching project remounts the workspace, so nothing typed or selected carries over. |
| Publishing | Each project publishes to ITS OWN repository, Confluence space and Jira project (project settings → integrations). The tool connector runs every call in a per-call scope so concurrent projects cannot see each other's target; a configured Jira project overrides a key the model picks; a project's repository must belong to the same organisation as the platform's token. A project with no target configured falls back to the platform default — **give every project its own repository** so two projects never write the same paths. |
| Authorisation | Every project route (including gate states) requires membership of the project in the URL. |

Not isolated by design: enterprise standards (`global` scope), platform templates, prompts, skills and steering files.
