# FlowPilot-AI

Autonomous Sales & Business Automation Agent

## Project structure

```text
backend/                FastAPI application and Dockerfile
db/                     PostgreSQL schema and initialization
n8n/workflows/          Importable n8n workflows
docs/                   Additional project documentation
docker-compose.yml      Local service orchestration
.env.example            Safe environment-variable template
```

## Phase 2: evidence-based company discovery

Phase 2 accepts an Ideal Customer Profile (ICP), searches for matching
companies with Tavily, optionally extracts a small number of candidate
websites, applies a heuristic website-validation status, and saves the
companies and source evidence in PostgreSQL. The status is a review aid, not
proof that a domain belongs to a company.

### Configure the ICP and run discovery

The ICP tells discovery what to look for. `target_industries` and
`target_geographies` define the market; `company_types`, `service_keywords`,
and `additional_keywords` refine the search. `include_domains` and
`exclude_domains` constrain candidate domains.

Example request (also used for the initial test run):

```json
{
  "target_industries": ["web development agency"],
  "target_geographies": ["Austin, Texas, United States"],
  "company_types": ["agency"],
  "service_keywords": ["web development", "AI automation"],
  "additional_keywords": ["B2B"],
  "include_domains": [],
  "exclude_domains": [],
  "max_queries": 1,
  "max_results_per_query": 3,
  "extract_top_n": 1
}
```

### Configure Tavily and rebuild the API

1. Copy the example environment file and edit `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Create a Tavily account and copy its API key from the Tavily dashboard into
   `TAVILY_API_KEY` in `.env`. Keep the key private; `.env` is ignored by Git.
   The optional settings in `.env` are:

   ```dotenv
   TAVILY_API_KEY=your_tavily_api_key
   TAVILY_BASE_URL=https://api.tavily.com
   TAVILY_TIMEOUT_SECONDS=30
   ```

3. Build and start (or rebuild) the API service:

   ```powershell
   docker compose up -d --build api
   docker compose logs -f api
   ```

The API is published on `http://localhost:8000` by default. Check
`http://localhost:8000/health/ready` before making discovery requests.

### Discovery API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/discovery/search` | Run discovery with an ICP JSON body |
| `GET` | `/api/v1/discovery/runs/{run_id}` | Retrieve a run and its saved metrics |
| `GET` | `/api/v1/companies` | List companies; supports `limit`, `offset`, and `website_validation_status` |
| `GET` | `/api/v1/companies/{company_id}/sources` | List source evidence for one company |

Send the example ICP above to:

```text
POST http://localhost:8000/api/v1/discovery/search
Content-Type: application/json
```

For example, save the JSON request as `icp.json` and run:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/discovery/search `
  -ContentType application/json `
  -InFile .\icp.json
```

The response includes the run ID and status, query and source counts, number
of extracted URLs, Tavily credits, errors, and discovered companies. Use the
returned run ID with the run endpoint to retrieve the persisted run record.
Company list responses can be filtered, for example:

```text
GET http://localhost:8000/api/v1/companies?website_validation_status=needs_review
```

### Persisted data

- `discovery_runs` records each discovery attempt, its ICP and generated
  queries, status, result/company/extraction counts, Tavily credits, errors,
  and start/finish times. Failed runs are retained too.
- `company_sources` stores the evidence behind discovered companies: source
  URL/domain, query, title/snippet, search score, extraction status/content,
  validation status, and the run that found it. This keeps provenance
  available instead of retaining only a company name.

### Website validation statuses

`website_validation_status` is a heuristic classification:

- `candidate`: a search result that has not been validated with extraction.
- `likely_official`: extraction succeeded and the content contains at least
  250 characters, relevant industry/service terms, and a token matching the
  domain name. This is not verified domain ownership.
- `needs_review`: extraction failed, or extracted evidence did not satisfy all
  of the heuristic checks for `likely_official`.
- `rejected`: an allowed schema/filter value, but the current discovery
  classifier does not assign it automatically.

### n8n workflow

1. Start the local stack with `docker compose up -d`.
2. Open the n8n editor at `http://localhost:5678`.
3. Import `n8n/workflows/Lead Discovery.json` using the workflow import option.
4. Open the **HTTP Request** node and adjust its JSON body to your ICP if
   needed. The included workflow already posts a small example request to
   `http://api:8000/api/v1/discovery/search`; that service hostname works from
   within the Compose network.
5. Execute the workflow manually using **Execute workflow** and inspect the
   HTTP Request node's output for the run ID and metrics.

### Free quota and known limitations

Tavily's pricing page lists **1,000 API credits per month** on its free
Researcher plan (checked 2026-10-30). Credits reset on the first of each
month; requests stop when the allowance is exhausted. Check the Tavily
dashboard and current pricing for account-specific usage and plan changes.
Search/extract usage depends on the provider's request and depth rules; the
`tavily_credits` field records the usage reported by Tavily for each run.

Discovery deliberately limits each ICP to at most 3 queries, 10 results per
query, and 5 extracted URLs (the highest-ranked candidate per domain). The
included workflow uses a smaller one-query, three-result, one-extraction
example. Results are deduplicated by normalized source URL/domain, blocked
directory/social domains are excluded, and website status is heuristic.
The Phase 2 discovery pipeline discovers and stores company/source evidence;
it does not verify legal identity, guarantee contact details, or perform
email validation or outreach. Email validation is covered by Phase 3.

### Initial measured run

The following figures are from an actual local API run on **2026-10-30** using
the example ICP above with `max_queries=1`, `max_results_per_query=3`, and
`extract_top_n=1`:

| Metric | Observed result |
| --- | ---: |
| Run ID | `296c3523-3bc3-4044-8e62-98ca548d69a8` |
| Status | `completed` |
| Queries executed | 1 |
| Source results | 3 |
| Unique discovered domains | 3 |
| Extracted URLs/pages | 1 |
| Tavily credits reported | 2 |
| Errors | 0 |

These are a single observed run, not a throughput or quality guarantee.

## Phase 3 — Data Quality & Qualification

### Email Validation

- Email syntax validation
- Email normalization
- MX record checks
- A/AAAA fallback checks
- DNS error handling
- Role-based email detection
- Mailbox verification explicitly unsupported

### Deduplication

- Normalized domain matching
- Provider identity matching
- Case-insensitive email deduplication key
- Fuzzy business-name matching
- Manual duplicate review queue

### Lead Qualification

- Industry match: 30 points
- Service relevance: 25 points
- Business evidence: 20 points
- Profile completeness: 10 points
- Contact quality: 15 points

### Testing

- pytest unit tests
- Mocked DNS responses
- Email validation tests
- Deduplication tests
- Scoring tests
- Pydantic schema tests

### Important Limitations

- MX records do not prove individual mailbox existence.
- DNS failures are treated as unknown, not invalid.
- Fuzzy matches require manual review.
- Website availability does not determine lead quality.
