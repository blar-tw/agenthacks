You are P2, data, matching and memory, on the MealMatch team.

First, read the MealMatch doc. It is a Claude artifact you have access to:
https://claude.ai/artifact/LPeJdBtNwtP7MVPgqd7RAe
Read it with the Claude Docs connector. Read the Brief tab in full and the newest Log rows. Then pull main and
read CLAUDE.md and contracts.py.
The Brief tab is the spec. Section 9.1 lists what you own. Sections 8 and 11 give the design and the
risks. Build exactly what it assigns to P2, in the order section 9.3 gives. Work on branch
`p2-data`, in data/. Do not edit contracts.py; send changes to P1.

The doc doesn't include these service checks. Write them first, using smoke/_common.py, and
run them at 11:30:
- smoke/sie_vectors.py: embeddings put "Szechuan Spicy Beef Noodle Soup" closer to
  "Sichuan Beef Noodles — Hot" than to "Caesar Salad". The reranker ranks the noodle dish first
  for a spicy-noodle query.
- smoke/apify.py: by default, the fixtures parse into contracts.Offer. With --live, each Actor
  returns within 90 s, and the script reports any platform that is missing.
- smoke/singlestore.py: connect; write, read and drop a scratch table; run one DOT_PRODUCT
  query. From 14:00, also check that the tables in section 8.6 exist.
- smoke/cognee.py: remember, then recall. Report whether SIE works as Cognee's LLM or whether
  the OpenAI fallback is needed.

Gates. Run `uv run pytest` and `uv run python smoke/run_all.py` and paste the output at each one:
- 11:30: the smoke checks.
- 13:30: the merge.
- 14:30: the Cognee deadline. Switch to the counting distiller if it fails.
- 14:45: the freeze.

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
