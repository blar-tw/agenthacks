You are P3, testing, UI and analytics, on the MealMatch team.

First, read the MealMatch doc. It is a Claude artifact you have access to:
https://claude.ai/artifact/LPeJdBtNwtP7MVPgqd7RAe
Read it with the Claude Docs connector. Read the Brief tab in full and the newest Log rows. Then pull main and
read CLAUDE.md and contracts.py.
The Brief tab is the spec. Section 9.1 lists what you own. Section 10 covers Firedrill setup and
scenarios. Build exactly what it assigns to P3, in the order section 9.3 gives. Work on branch
`p3-sim-ui`, in sim_ui/. Do not edit contracts.py; send changes to P1.

The doc doesn't include these service checks. Write them first, using smoke/_common.py:
- smoke/statsig.py: check_gate on the ranker flag returns a bool, and log_event flushes
  without error.
- smoke/firedrill.py: the CLI is logged in, the custom tool is registered, and POST /chat
  answers. Report SKIP if the server is down.
- Answer the Firedrill open item in section 12 by 11:30. Write the answer in the Brief and tell P1.

Gates. Run `uv run pytest` and `uv run python smoke/run_all.py` and paste the output at each one:
- 11:30: the smoke checks.
- 13:30: run the happy-path drill on stubs.
- 14:00: the happy-path drill passes on fixtures.
- 14:45: the freeze.
After the freeze, run all scenarios and both rankers. Take the screenshots and record the
backup video, as section 9.3 says.

KEEPING THE DOC CURRENT
Follow the doc rules in CLAUDE.md. The doc has a Brief tab (the spec) and a Log tab.
- Before each task: read the Brief tab and the newest rows of the Log tab.
- After each task, and at each gate (11:30, 13:30, 14:00, 14:30, 14:45), after a merge, and
  whenever you get blocked: update any part of the Brief your work made out of date (schema,
  interfaces, nodes, cut features, swapped tools, fallbacks taken). Then add one row to the
  top of the Log tab:
  `HH:MM · P# · what changed (with the smoke/pytest result when relevant) · who it affects`.
- Read the doc again right before you edit it. Change only the parts you own and add your own
  rows. Never rewrite or delete another person's text.
- Never put keys, cookies, tokens or personal data in the doc.
Then tell me in one line what you changed in the doc.
