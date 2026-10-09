# Project connections

Every project can have its own Git repository, Jira project, Confluence space and knowledge-base settings.
Open a project and choose **Configure → Connections**. Each section has **Test connection** and **Save**.

| Section | You enter | The test checks |
|---|---|---|
| Git repository | `owner/name`, branch, API address (GitHub Enterprise only), access token | token accepted · repository reachable · write access · branch exists |
| Jira | site address, project key, account email, API token | credentials accepted · project found · can create issues |
| Confluence | site address, space key, email, API token (or "same account as Jira") | credentials accepted · space found |
| Knowledge base | which sources the agents may search (standards, approved artefacts, codebase) and how many snippets | what is indexed per source · a sample search returns results |

* **Test uses what is on the screen**, saved or not. A blank token field uses the saved token.
* **Tokens are write-only.** They are stored encrypted (`project_connections.secret_enc`, Fernet), never returned by the API,
  never written to the audit log, and shown only as "saved". Remove one with "Remove the saved token".
* **No token of its own?** The section keeps using the platform's shared connection (the tool connector's environment). The
  screen says so; it cannot test the shared one.
* **Publishing uses them.** When the tool connector publishes for a project, the project's own token and address go with that
  call, so each project writes to its own repository / site, even in a different organisation. The same-organisation limit on
  repositories is lifted when the project brings its own GitHub token.
* **Knowledge base switches** apply to every search the agents run for the project.

Who can change them: the managing project manager or a super-admin. Everyone on the project can see the settings (never the tokens).

## Configuration

| Setting | Meaning |
|---|---|
| `CONNECTIONS_KEY` | Encryption key for the stored tokens. Defaults to a key derived from `JWT_SECRET`; set a dedicated one in production, because changing it makes saved tokens unreadable. |
| `CONNECTIONS_ALLOW_PRIVATE_HOSTS` | Default `false`: the server refuses to call loopback, private or link-local addresses and requires https. Set `true` for self-hosted Jira/Confluence on a private network. |

## API

`GET /api/projects/{id}/connections`, `PUT /api/projects/{id}/connections/{github|jira|confluence|kb}`,
`POST /api/projects/{id}/connections/{kind}/test`. Migration `0040_project_connections.sql`.

## Not yet

* Git is GitHub only (including GitHub Enterprise Server); GitLab and Bitbucket are not supported.
* The knowledge base is the platform's own index; an external knowledge source (for example a Confluence space) is not indexed yet.
* A shared platform connection cannot be tested from this screen.
