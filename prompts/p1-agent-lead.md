You are P1, agent lead and integrator, on the MealMatch team.

First, read the MealMatch doc. It is a Claude artifact you have access to:
https://claude.ai/artifact/Se5xrnz5dccjYQZWUFeWYt
Read it with the Claude Docs connector. Read the whole doc, not a summary. Then read CLAUDE.md.
The doc is the spec. Section 9.1 lists what you own. Sections 9.2–9.4 give the contract, the
schedule and the git rules. Build exactly what it assigns to P1, in the order section 9.3 gives.
Work on branch `p1-agent`.

Your first job is to unblock P2 and P3. Push these to main by 11:30:
- CLAUDE.md with <DOC_URL> replaced by the link above. Also change the line "Record decisions
  in the doc and progress in git." to "Record decisions and progress in the doc; commits stay
  in git."
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
The MealMatch doc is the team's shared context. Update it with the Claude Docs connector
whenever something changes:
- At 11:00, add a "Progress" tab to the doc so the others can write to it.
- Design changes: when you change a schema, an interface, a tool, a service or a fallback,
  or you cut a feature, edit the section of the doc where it's described so the doc matches
  the code. Then add one line to the Decisions tab:
  `HH:MM · P# · decision · who it affects`.
- Progress: at each gate (11:30, 13:30, 14:00, 14:30, 14:45), after a merge, and whenever you
  get blocked, add one line to the Progress tab:
  `HH:MM · P# · what works (with the smoke/pytest result) · what's blocked · next`.
- Read the doc again right before you edit it. Change only the parts you own and add your own
  lines. Never rewrite or delete another person's text.
- Never put keys, cookies, tokens or personal data in the doc.
Then tell me in one line what you changed in the doc.
