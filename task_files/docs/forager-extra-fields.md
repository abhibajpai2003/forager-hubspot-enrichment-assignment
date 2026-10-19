# Extra Forager fields worth discussing

The brief asks for notes on "interesting extra details" Forager surfaces beyond the required
fields. This list comes from Forager's OpenAPI spec. Fill in the example values from the live
OpenAI run.

## Already stored

These arrive in the same role search we already pay for, so they cost nothing extra.

| Field | HubSpot property | Example (from live run) | Why it's useful |
|-------|------------------|-------------------------|-----------------|
| LinkedIn headline | `linkedin_headline` | | One-line summary of what the person does; good for personalising outreach |
| LinkedIn "About" | `linkedin_about` | | Their own description of their work and interests |
| Skills | `linkedin_skills` | | Signals what they work on day to day |
| Current role start date | `current_role_start` | | New in role = often re-evaluating vendors |
| Company keywords | `forager_keywords` | | What the company says it does, beyond one industry label |
| Employee range | `forager_employees_range` | | Bucketed size, steadier than an exact headcount |
| LinkedIn industry | `forager_industry` | | Kept even when it doesn't match HubSpot's industry dropdown |

## Available, not stored yet

### Person: one person detail lookup, 1 credit each

| Field | What it is | Worth adding? |
|-------|------------|---------------|
| Work history (`roles`) | Every past employer, title and dates | **Yes.** "Used to work at a customer" is one of the strongest sales signals |
| Education (`educations`) | School, degree, field of study, dates | Maybe. Useful for rapport, not targeting |
| Languages | Language and proficiency | Maybe, for regional teams |
| Certifications | Name, issuer, date | Maybe, for technical buyers (e.g. cloud certifications) |
| Publications, patents, projects | Titles, links, dates | Niche. Useful when selling to research teams, like OpenAI's |
| Volunteering, honors, courses, test scores | | No, low sales value |
| Photo URL | Profile photo link | Optional, cosmetic |
| Gender | | **No.** Sensitive and not needed for outreach |

### Person: separate paid lookups

| Field | Cost | Worth adding? |
|-------|------|---------------|
| Phone numbers | 15 credits | Required by the brief; switched off for the 50-credit budget (`ENABLE_PHONE_LOOKUP`) |
| Personal emails | 5 credits | Usually no. Work email is the right channel for B2B outreach |

### Company: organization search and related endpoints

| Field | What it is | Worth adding? |
|-------|------------|---------------|
| Funding events | Round type, amount, date (when a funding filter is used) | **Yes.** Recently funded companies are buying |
| Job posts | Open roles, titles, remote or not | **Yes.** Hiring for a role you sell to is a buying signal |
| Web technologies | Tech stack detected on the company website | **Yes,** for selling tools that integrate with or replace a technology |
| Domain rank / traffic | Website popularity | Maybe, as a size and momentum proxy |
| Operating status, legal name, logo | | Low value; logo is cosmetic |
| Product Hunt launches, SEC Form C/D filings | Simple events | Niche, mostly for startup-focused sales |

## Recommendation

Add **work history** for contacts (1 credit per person) and **funding events, job posts and web
technologies** for companies. These are the fields most likely to change who sales calls and
what they say. Leave out gender, test scores and personal emails.
