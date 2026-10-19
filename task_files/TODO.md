# TODO: Forager × HubSpot Enrichment

The **Credits** notes show how many Forager credits a step spends. Everything except the final
demo runs against mocks or the response cache at 0 credits.

---

## Decisions

| Question | Decision |
|----------|----------|
| OpenAI company created by hand or by script? | By hand, so the webhook fires live on video |
| Which 5 job titles? | Any. Default: Account Executive, Recruiter, Solutions Engineer, Partnerships, Customer Success. Confirm with the free `totals` check |
| Store the nice-to-have fields? | Yes: headline, About, skills, current role start |
| Credits | 50 available. **Phone lookups skipped** (15 each). Budget cap set to 45 |

### Credit budget

Prices from Forager's [API Credit Pricing](https://docs.forager.ai/api-overview/credit-pricing).
Work email lookups are charged only when an address is found, so misses are free.

| Step | Credits |
|------|--------:|
| Totals checks (choose titles) | 0 (to confirm) |
| OpenAI organization search | 1 |
| Person role search (OpenAI + 5 titles) | 1 |
| 5 work emails × 5 | 25 |
| **Primary deliverable** | **27** |
| Job-title filtering demo (2 previews) | 2 |
| Contact webhook demo (name match 1 + email 5) | 6 |
| **Planned total** | **35** |
| Buffer | 10 (budget cap 45) + 5 unbudgeted |

---

## ✅ Done: code (0 credits)

- [x] Project skeleton: uv, `pyproject.toml`, ruff, mypy (strict), pytest
- [x] Config from env vars (`app/config.py`), `.env.example`, `.gitignore` (also keeps the
      confidential PDF out of Git)
- [x] Forager client: all endpoints, retries, async work email polling, credit ledger with hard
      budget, on-disk response cache, `credits_balance()`
- [x] Forager mock (`FORAGER_MODE=mock`) built from Forager's OpenAPI spec
- [x] HubSpot client: objects, search, v4 associations, properties, 409 duplicate handling
- [x] Field mapping and cleaning: domains, names, location split, industry dropdown matching,
      never overwrite with empty values
- [x] Company flow, contact flow (LinkedIn → name+company → email matching), contact finder
      (round-robin titles, skip people without email, preview mode)
- [x] Phone lookup behind `ENABLE_PHONE_LOOKUP` (off)
- [x] Webhook endpoint: v3 signature check (v1/v2 fallback), background jobs, event de-dupe,
      loop guard for finder-created contacts, delay before processing
- [x] Admin endpoints: `/enrich/company`, `/enrich/contact`, `/companies/{id}/find-contacts`,
      `/credits`
- [x] Scripts: `setup_hubspot_properties`, `forager_check accounts|totals`
- [x] Dockerfile, `.dockerignore`, `railway.json`
- [x] 80 tests passing; ruff and mypy clean
- [x] App started with uvicorn in mock mode; health, signature rejection, signed webhook and
      admin auth checked with curl
- [x] README (setup, deploy, demo runbook) and `docs/forager-extra-fields.md`

## ⏳ Remaining: needs your accounts

### Accounts and keys

- [ ] Forager: put the API key in `.env`, then run
      `uv run python -m scripts.forager_check accounts` to get `FORAGER_ACCOUNT_ID`.
- [ ] HubSpot: create a free developer test account, then a legacy private app with the scopes
      in the README. Copy the access token and client secret into `.env`.
- [ ] Run `uv run python -m scripts.setup_hubspot_properties`. Check that it reports no missing
      standard properties.
- [ ] HubSpot: consider turning off automatic company creation and company auto-fill (README,
      Setup step 2.6).
- [ ] GitHub: create a repository and push. The `.gitignore` already excludes `.env`, the cache
      and the PDF.
- [ ] Railway: create a project from the repository.

### Before spending credits

- [ ] `forager_check totals --domain openai.com --titles ...`: confirm the balance doesn't
      change (totals are free), and pick 5 titles that all have people.
- [ ] Docker build: not tested locally because this machine has no Docker permission. Railway
      builds it on the first deploy; check the build log there.

### Local test with real HubSpot (0 credits, mock Forager)

- [ ] Run locally with `FORAGER_MODE=mock`, expose it with `ngrok http 8000`, and set
      `PUBLIC_BASE_URL` to the ngrok URL.
- [ ] Point the private app's webhook Target URL at `<ngrok>/webhooks/hubspot`. Subscribe to
      Company → Created and Contact → Created.
- [ ] Create a throwaway company with a domain. Check the logs and that fake data and contacts
      appear in HubSpot.
- [ ] Delete the throwaway records.

### Deploy (0 credits)

- [ ] Railway: add a volume at `/data`, generate a domain, set all variables (README Setup
      step 3), keep `FORAGER_MODE=mock`.
- [ ] Change the webhook Target URL to `https://<app>.up.railway.app/webhooks/hubspot`.
- [ ] Repeat the throwaway-company test against Railway, then delete those records.

### First live call (1 credit, from your machine)

- [ ] `uv run python -m scripts.forager_check preview --domain openai.com --titles <your 5>`.
      This doesn't touch HubSpot. Check the mapped fields for each person: city, state and
      country split correctly, LinkedIn URLs present, titles sensible.
- [ ] If anything looks wrong, fix the mapping before the demo. Don't create OpenAI in HubSpot
      yet: in live mode that triggers the paid flow.
- [ ] The work email format is checked on the first real lookup during the demo. If Forager's
      format isn't recognised, the finder stops after one lookup (5 credits) instead of
      continuing. The raw response is cached, so after a parser fix the retry is free.

### Demo recording (about 35 credits; server budget 45 + 1 from the preview = 46 of 50)

- [ ] Switch Railway to `FORAGER_MODE=live`.

Follow the **Demo runbook** in the README:

- [ ] Create OpenAI (`openai.com`) by hand. The webhook enriches it and creates 5 contacts with
      emails.
- [ ] Show every field on the company and the 5 contacts.
- [ ] Job-title filtering: two `preview` calls with different titles.
- [ ] Contact webhook: create one person by hand, associated with OpenAI.
- [ ] `GET /credits` to show the total spent.

### Submission

- [ ] Fill in the example values in `docs/forager-extra-fields.md` from the live run.
- [ ] Edit and upload the video.
- [ ] Send the Railway URL and repository access.
- [ ] Give reviewers access to the HubSpot records: invite them to the portal, or send
      screenshots.
- [ ] Explain the phone skip: 50 credits, 15 per phone, supported behind `ENABLE_PHONE_LOOKUP`.
