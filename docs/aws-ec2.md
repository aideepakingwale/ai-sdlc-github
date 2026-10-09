# Running DevMind on AWS EC2

A single-host deployment: one EC2 instance runs the whole stack with Docker Compose and calls Amazon Bedrock through the instance's IAM role
(no API keys). `deploy.sh` does the configuration, build, migrations, seed and start; it is safe to re-run. For anything beyond a single team,
move Postgres, Redis, DynamoDB and S3 to managed services and terminate TLS at an ALB (see `infra/k8s`).

## 1. Launch the instance

| Setting | Value |
|---|---|
| AMI | Ubuntu 22.04 LTS |
| Type | **t3.large or bigger (8 GB)**. t3.medium (4 GB) runs, but the first build needs the swapfile and generation workers compete for memory. Use `--no-diagrams` to save about 600 MB |
| Disk | 40 GB gp3 (images, Postgres, the content store) |
| IAM instance profile | A role with `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on the models you use, including the **inference profile** (`us.anthropic...`) and the foundation models behind it in every region the profile routes to |
| Metadata options | **Hop limit = 2** (containers must reach the instance role). IMDSv2 required is fine |
| User data | `infra/aws/ec2-user-data.sh` (installs Docker, swap, git, AWS CLI). Optional: fill `REPO_URL` and `BEDROCK_MODEL_ID` to deploy on first boot |
| Security group | Inbound **3000** (the app) from your office or VPN range, **22** from your IP. **Do not open 8080, 8180, 9000, 5432, 6379**: the compose file publishes some of them for debugging |

Enable the model in Bedrock (Model access) in the same region before the first run.

## 2. Deploy

```bash
ssh ubuntu@<public-ip>
git clone https://github.com/aideepakingwale/ai-sdlc-github.git /opt/ai-sdlc && cd /opt/ai-sdlc
./deploy.sh --bootstrap --model-id us.anthropic.claude-sonnet-4-6 --region us-east-1
# optional: --light-model-id <a Haiku inference profile>  for the validator, fact check, clarifier and other small calls
```

What it does: writes `.env` (generates `JWT_SECRET` and `CONNECTIONS_KEY`, sets Bedrock, `AUTH_MODE=local`, the public URL), writes
`docker-compose.override.yml` so the AI client uses the instance role, checks Bedrock is reachable, builds the images, applies **every migration**
(`docker compose run --rm migrate`, including `0039`–`0042` for memory, connections, codebase archive and artefact runs), seeds the demo users,
and starts the stack.

Open `http://<public-ip>:3000`. Seeded users (password `Password123!`, **change or remove them before real use**): `superadmin@sdlc.local`,
`sa@`, `ta@`, `qa@`, `devops@`, `dev@sdlc.local` and a project manager.

## 3. First-run checklist

1. **Observability → AI configuration**: confirm the Bedrock model and region; tick **Per-artifact parallel generation** so each stage is written by its
   specialist agents (Governance → Agents lists them). Optionally set the model routes (reasoning, generation, fast, planning).
2. Create a project, run stage 1, open an artefact and check **How this was made** shows the agent and the model.
3. **Connections** (per project): add the project's repository, Jira and Confluence addresses and tokens, then **Test connection**. A project with no token of
   its own uses the shared connection configured on the tool connector (`JIRA_*`, `CONFLUENCE_*`, `GITHUB_*` in `.env`).
4. Cost Explorer: Bedrock cost is billed per model and region. To separate this app from other Bedrock users on the account, create an
   application inference profile with a cost-allocation tag and use its ARN as `--model-id`.

## 4. Updating an existing instance

```bash
cd /opt/ai-sdlc && git pull origin main
./deploy.sh --model-id <the same id> --region <the same region>
```

Re-running keeps your `.env` secrets, rebuilds the changed images, applies new migrations and restarts. Use `--skip-build` only when nothing in
the code changed. Prompts, skills, steering and **agents** are bind-mounted from the checkout, so editing a file under `services/orchestrator-py/agents/`
and restarting the orchestrator (`docker compose restart orchestrator`) is enough to tune an agent.

## 5. Operating it

* **Secrets**: `.env` holds `JWT_SECRET` and `CONNECTIONS_KEY`. Back it up. Losing `CONNECTIONS_KEY` makes every saved project token unreadable (they
  must be entered again); changing `JWT_SECRET` signs everyone out.
* **Data** lives in Docker volumes (`pgdata`, `redisdata`, `dynamodata`, `contentstore`). Snapshot the EBS volume, or `pg_dump` the `sdlc` database
  and copy `contentstore` to S3 on a schedule.
* **Logs**: `docker compose logs -f orchestrator ai-client tool-connector`. Admins also have a live log viewer in the app.
* **Health**: `docker compose ps`; `curl localhost:8080/healthz` on the instance.
* **TLS**: the app serves HTTP on 3000. Put an ALB with an ACM certificate in front (then set `APP_PUBLIC_URL` to the https address and re-run
  `deploy.sh`), or use `docker-compose.tls.yml` with a certificate on the instance (`scripts/gen-selfsigned.sh` for a test certificate).
* **Self-hosted Jira or Confluence** on a private network: set `CONNECTIONS_ALLOW_PRIVATE_HOSTS=true` in `.env` and restart the orchestrator. Leave it
  `false` otherwise: it stops the server being pointed at internal addresses.
* **Authentication**: `deploy.sh` uses local accounts (`AUTH_MODE=local`). For single sign-on use the Keycloak service in the compose file and
  `AUTH_MODE=keycloak`.

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Stages fail with an AWS credentials or access-denied error | Instance profile attached, hop limit 2, model access granted, and the IAM policy covers the inference profile and the underlying models in each region |
| Bedrock cost shows other models or regions | Cost Explorer groups by invoked model and region. Filter by linked account and role, or use an application inference profile with a tag |
| Build killed or very slow | Out of memory: use a larger instance, keep the swapfile, or pass `--no-diagrams` |
| `Applied migrations` missing a new one | `docker compose run --rm migrate` and read its output; migrations apply in file order |
| A saved token "disappeared" | `CONNECTIONS_KEY` changed. Restore the old value or re-enter the tokens |
| Page loads but actions fail | `docker compose logs orchestrator`; confirm port 3000 is the only one you use and `APP_PUBLIC_URL` matches the address you browse to |
