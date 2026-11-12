# Security

CashLens handles bank statements, so a security report is taken seriously and answered.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: open the **Security** tab of this repository and
choose **Report a vulnerability**. That reaches the maintainer privately. Please do not open a
public issue for anything that could expose someone's financial data.

Include what you found, how to reproduce it, and what you think the impact is. You will get an
acknowledgement, and a fix or an explanation, as quickly as the maintainer can manage; this is a
one-person project, so please allow a few days.

## What is in scope

- The API under `services/api` and the web app under `apps/web`.
- The database migrations, Row Level Security policies and storage rules under `db/`.
- The deployed instance, within the bounds of your own data: please test only against accounts
  and uploads you own, and never against another person's workspace.

## What is deliberately public

- The Supabase project URL and the publishable key are shipped to the browser by design; they
  grant nothing on their own because every table is protected by Row Level Security and the API
  holds the only privileged credential server-side.
- The API publishes its OpenAPI schema in development only.
