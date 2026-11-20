You are P1, agent lead and integrator, on the MealMatch team.

First, read the MealMatch doc. It is a Claude artifact you have access to:
https://claude.ai/artifact/LPeJdBtNwtP7MVPgqd7RAe
Read it with the Claude Docs connector. Read the Brief tab in full and the newest Log rows. Then read CLAUDE.md.
The Brief tab is the spec. Section 9.1 lists what you own. Sections 9.2–9.4 give the contract, the
schedule and the git rules. Build exactly what it assigns to P1, in the order section 9.3 gives.
Work on branch `p1-agent`.

Your first job is to unblock P2 and P3. Push these to main by 11:30:
- The uv project, the folders agent/, data/, sim_ui/ and smoke/, a .gitignore that covers .env,
  and .env.example with variable names only.
- contracts.py with stubs, as section 9.2 describes.

The doc doesn't include these service checks; add them:
- smoke/_common.py: a check helper. It prints PASS/FAIL with a reason, never prints a secret,
  and exits non-zero on failure.
- smoke/run_all.py: runs every smoke/*.py. With --keys-only it checks only that each env var exists.
- smoke/sie_chat.py: SIE chat returns a tool call that Pydantic accepts. Run it once with SIE
  and once with the Claude Sonnet fallback.
- tests/test_resume.py: POST /chat pauses at select_option. A second POST with the same
  thread_id resumes the run.
- smoke/audit_integrity.py, before 14:45: every inference event cites evidence IDs that exist,
  and no submit happens without an earlier approval in the same run.

Gates. Run `uv run pytest` and `uv run python smoke/run_all.py` and paste the output at each one:
- 11:30: keys and the SIE check.
- 13:30: the merge.
- 14:00: the checkpoint in section 9.5.
- 14:45: the freeze.
If a check fails, take the fallback in the doc's cut list.

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
