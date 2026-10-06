# Technology stack

A project is created **without** a technology stack. Creating a project asks for a
name, the pipeline and (optionally) the GitHub / Jira / Confluence targets - not a
language, version or framework.

The **Technical Architect** stage decides the stack:

1. If the request, the attached documents or the upstream artefacts (requirements,
   solution architecture, ADRs) state or clearly imply one, the Technical Architect
   adopts exactly that.
2. If nothing does, the stage **asks the reviewer** before generating - a language
   and runtime-version question with the catalogue's languages as options, plus
   **"Recommend one for me"**. (The question is asked once; the answer is recorded.)
3. With "Recommend one for me", or when the stage is configured not to ask
   (`CLARIFY_ENABLED=false`), the Technical Architect chooses the simplest mainstream
   stack that satisfies the requirements and justifies it in a **Technology stack
   decision** section of the LLD, whose first line is machine-readable:
   `**Stack:** Python 3.12 + FastAPI | PostgreSQL 16`.
4. When the stage finishes, the platform reads that line (falling back to the language
   most prominent in the design text) and records it on the project
   (`projects.tech_stack`, `tech_stack_source = 'ta'`), audited as `project.stack_decided`.
   Every later stage - QA, DevOps, Developer - targets it. If nothing can be determined
   the stack stays undecided and the reviewer can set it (below).

Until it is decided, every earlier stage (requirements, solution architecture) is told
the stack is **not decided yet** and to stay technology-neutral rather than assume one;
the clarification check does not ask those personas for a language or framework.

## Setting it by hand

The project's context panel shows the stack (or "Not decided yet"). The managing PM, a
super-admin or the project's Technical Architect can **Set manually** / **Change** /
**Clear** it (`PUT /api/projects/{id}/tech-stack`). A stack set by hand is never
overwritten by a later Technical Architect run; a stack the Technical Architect decided
may be revised by its next run (for example after "use Go instead" feedback).

## Existing projects

Projects created before this change keep the stack they already have. The Technical
Architect stage may still revise it (it was never set by hand).
