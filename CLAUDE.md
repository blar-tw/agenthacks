# MealMatch

MealMatch is a personal food-ordering agent. It compares Uber Eats and
DoorDash, remembers the user's preferences, and prepares a verified cart
that the user pays for themselves. We are building it in a 5.5-hour
hackathon: a simple version that works end to end beats a complete one
that doesn't.

## Shared context: the MealMatch doc

The team's shared context layer is the MealMatch doc: https://claude.ai/artifact/LPeJdBtNwtP7MVPgqd7RAe.
Read and write it through the Claude Docs connector.

- **Read it** before you start a task, and whenever a question touches
  the design, the database schema, an interface, or another person's folder.
- **Write to it** when you make a decision that affects someone else:
  a schema or interface change, a cut feature, a swapped tool or service.
  Update the tab where that content lives so the doc stays current, and
  add one entry to the **Decisions** tab:
  `HH:MM · who · decision · who it affects`.
- Record decisions in the doc and progress in git.

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
