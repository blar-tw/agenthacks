# MealMatch

MealMatch is a personal food-ordering agent. It compares Uber Eats and
DoorDash, remembers the user's preferences, and prepares a verified cart
that the user pays for themselves. We are building it in a 5.5-hour
hackathon: a simple version that works end to end beats a complete one
that doesn't.

## Shared context: the MealMatch doc

The team's shared context layer is the MealMatch doc: https://claude.ai/artifact/LPeJdBtNwtP7MVPgqd7RAe.
Read and write it through the Claude Docs connector.

- **Before you start a task, read it**: the Brief tab, and the newest
  rows of the **Log** tab to see what your teammates changed.
- **After you finish a task, update it**: change any part of the Brief
  your work made out of date (schema, interfaces, nodes, cut features,
  swapped tools), then add one row to the top of the **Log** tab:
  `HH:MM · who · what changed · who it affects`.

## Audit trail

The audit trail is the product. Every LangGraph node appends an audit
event with a `kind`: `evidence` (scraped or stored facts, with source and
timestamp), `inference` (anything the agent concluded: dish matches,
learned preferences, rankings), `action` (tool calls that change state),
or `approval` (the user's explicit yes or no). Every recommendation cites
the IDs of the evidence events behind it.

## Secrets

Session cookies, passwords, session tokens and payment details stay
inside the local Playwright profile. Keep them out of LLM prompts, Apify
inputs, Amplitude and Statsig events, SingleStore rows and logs.

## Tooling

Python dependencies are managed with uv (`uv add`, `uv run`).
