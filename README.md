# Debt Manager

A Streamlit app for people carrying debt: enter your cards and loans, see exactly
where the money is going, and find out what it would actually take to get out.

## What it does

- **Four clear destinations.** *Home* says what to do next, *Accounts* keeps
  balances and terms current, *Plan* lets you test and save a payoff approach,
  and *Activity* records what you actually paid. Account settings stay in a
  separate, visually secondary section.
- **Two debt shapes, modelled properly.** Credit cards use a percent-of-balance
  minimum with a dollar floor — so the minimum *shrinks as you pay*, which is the
  trap. Term loans use a fixed contractual payment. The user never declares
  which is which: they pick a **type** (Credit card, Auto, Student, Mortgage…)
  and `models.kind_for` maps it, because the card/loan split is our modelling
  distinction and not a question anyone should answer before typing a balance.
- **A real amortization engine.** One code path (`debtapp/engine.py`) produces
  every number in the app, so nothing on screen can disagree with anything else.
  Validated against closed-form amortization tables. Credit cards can also carry
  a remaining 0% promotional term: projections charge no interest through that
  window, switch to the regular APR afterward, and show whether the planned
  payments clear the card before the promotion ends.
- **Due dates and a payment ledger.** Give each account the day of the month it's
  due and the app tracks the calendar: what's coming up, what's past due, and a
  one-click *"I paid this"* that records the payment and reduces the balance.
  The **Ledger** page then counts what the debt has really cost you — total paid,
  how much of it the lender kept, and the cents-on-the-dollar split per account.
  Those figures are recorded history, never re-derived, which makes them the only
  numbers in the app that carry no assumptions.
- **Visualizations**: balance projection, the cents-on-the-dollar split, interest
  by year, payoff timeline, per-account interest, a strategy bake-off, and the
  ledger's actual-payments history. Each one sits on its own card with the title
  in the card header — Plotly lays a title and a horizontal legend into the same
  strip of top margin, so a chart that draws its own title crowds its legend.
  Only the projection is on the page by default; the rest are under *More
  detail*, because four of them restate what the tiles already say in words.
- **~15 quantified insights**, ranked by dollars at stake — overdue payments and
  what a miss really costs, balance transfers net of fees, consolidation,
  utilization and its credit-score cost, debt-to-income, extra payments (one
  card with a ladder of tiers, not one card per tier), windfall timing, the cost
  of waiting six months.

  Home shows the highest-value next moves; Plan shows the **top one** and folds
  the rest into a drawer. Supported recommendations open the right page with
  the suggested amount or strategy already filled in, ready for review rather
  than changing saved data behind the user's back. Ranking
  by dollars is only worth something if the ranking is allowed to decide what
  you read first; a wall of twenty cards spends it. Each card also carries a
  `recoverable` flag for anywhere that totals them — a stake like "minimums
  would cost you $77,876" ranks its card but is not money any action recovers.
- **One what-if lever**: how much extra per month, priced in months saved and
  interest saved. A useful preview can be saved as the monthly plan in one
  explicit click; until then it changes nothing.
- **Accounts and persistence** so you can come back and pick up where you left
  off, plus a progress chart across check-ins. Closing an account is its own
  decision rather than a cell edit: open its focused form on **Accounts** and either
  mark them paid off — balance and payment to zero, history kept, gone from
  every projection — or remove them outright behind a confirmation. Removing
  never touches the ledger, because what a card already cost you is a fact
  about your past, not about whether you still hold the card.

## Authentication

- **bcrypt** password hashing, with a **timing decoy** so a non-existent email
  costs the same as a real one — otherwise response time enumerates your users.
- **Throttling**: 5 failed attempts locks that email for 15 minutes, and the
  lockout applies to *unregistered* emails too (throttling only real accounts
  would itself reveal which addresses exist). A correct password clears the
  strikes so a user isn't locked out by their own typos.
- **Password policy** in `security.py`: 10-character minimum, a blocklist of
  the passwords that actually appear in credential-stuffing lists, and rejection
  of keyboard runs, repeated characters, and passwords containing your email.
  Length is weighted over symbol soup, because that is the habit worth pushing.
  Passwords over bcrypt's 72-byte limit are rejected rather than silently
  truncated.
- **Sessions** are opaque 256-bit tokens; only their SHA-256 hash is stored, so
  a leaked database yields no replayable logins. They expire on both an absolute
  clock (30 days) and an idle clock (7 days), and "keep me signed in" is opt-in
  — unchecked gives you 12 hours. Changing your password revokes every session.
- **Recovery codes** — eight single-use codes issued at signup, stored hashed.
  There is no mail server here, so these are the entire account-recovery story
  and the app makes you acknowledge them before it lets you in.
- **Sign-in history** on the Account page, so a user can spot attempts they
  don't recognise.

> **Known tradeoff:** the session token lives in the URL query string, because
> Streamlit has no first-party way to set a cookie. A URL can leak through
> browser history, referrer headers, or a shared link. It is mitigated (hashed
> at rest, dual expiry, short-by-default, instantly revocable) rather than
> solved. If you deploy this somewhere that matters, put a real identity
> provider in front of it via `st.login()` / OIDC, or add a cookie component.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Create an account on first load. Tick "start with example debts" to get a
realistic portfolio you can edit; leave it unticked and the app asks you for one
account — name, type, balance, rate, payment — and projects it live as you type,
before you save anything.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The suite covers engine math against closed-form amortization answers, insight
correctness, due-date arithmetic (month-end clamping, leap years, paid-early vs
not-yet-paid), ledger aggregation and persistence, the full auth surface
(policy, throttling, session expiry, recovery codes), and end-to-end UI tests
that drive every page and the login screen through Streamlit's `AppTest` —
including empty accounts, zero-APR loans, negative-amortization plans, payments
left orphaned by a deleted account, the first-run quick-add, and the grid's
focused account editing and backward-compatible persistence.

## Persistence and deployment

`DEBTMANAGER_DB` selects the backend by URL scheme:

| Value | Backend |
|---|---|
| unset | SQLite at `data/debt.db` — zero setup, for local dev and tests |
| a filesystem path | SQLite at that path |
| `postgresql://…` | Postgres, via a pooled connection |

> **Deploying to Streamlit Community Cloud you must use Postgres.** The container
> filesystem is **ephemeral** — wiped whenever the app sleeps, reboots, or
> redeploys — so a SQLite file there silently loses every account. Provision a
> database (Neon's free tier is enough), then paste its **pooled** connection
> string into the app's **Settings → Secrets** as `DEBTMANAGER_DB`.

`debtapp/db.py` is the only module that touches storage. Queries are written once
with `?` placeholders and rewritten for Postgres; the schema is a single template
with the three type differences (identity columns, float type, email collation)
substituted per backend, so the two cannot drift apart.

Pooling lives in `debtapp/_pools.py`, one pool per DSN for the life of the
process — deliberately a separate module, because the tests reload `db` and a
pool held there would leak connections on every reload. `max_idle` sits below
Neon's five-minute autosuspend so connections are retired before the server drops
them, and the pool revalidates on checkout; otherwise the first request after an
idle spell gets handed a dead socket.

Set `DEBTMANAGER_TEST_DB` to a Postgres URL to run the suite against Postgres
instead of SQLite. The same suite runs on both.

## Design notes

- **Account editing is focused and explicit.** The list stays scannable; opening
  one account reveals the six common statement fields and keeps model-specific
  details in an expander. Saving preserves the account ID and every persisted
  field, so existing users do not lose history or advanced settings.
- **0% APR means zero during the window, not forever.** A card can store its
  regular APR plus the number of interest-free billing cycles remaining. The
  engine uses 0% through that term, changes rate the following month, and moves
  the card within an avalanche plan when its active APR changes.
- **Money math** is monthly-compounded and rounded to the cent each month, the
  way a servicer actually posts. A 60-month loan can therefore end with a
  sub-dollar 61st stub payment — that is correct, not a bug.
- **History and projection are kept apart.** `engine.py` projects; `payments.py`
  records. A projection moves whenever an assumption moves, so the ledger never
  recomputes from one — it only ever sums payments the user actually logged.
- **A due date that has passed doesn't nag forever.** Someone who never logs
  payments sees a countdown, not a permanent red banner; the "overdue" warning
  only holds to the next cycle for users whose history shows they do log them.
  A warning people learn to ignore is worse than no warning.
- **Insights never quote a savings figure against a plan that never terminates.**
  If minimum payments would never clear the debt, the app says so and reports
  what you'd have paid and still owe, rather than inventing a number from
  wherever the simulation stopped.
- **Charts** follow a validated palette (colorblind-safe, checked in both light
  and dark against their own surfaces). Every chart ships a table view, a legend
  for two or more series, and selective direct labels rather than a number on
  every point.
- **Every string bound for markdown goes through `ui.common.esc`.** Streamlit
  renders `$…$` as LaTeX, so a sentence carrying two money figures — "**$75.07**
  comes off what you owe; **$174.93** goes to the lender" — silently collapses
  into a run of maths glyphs. Nothing throws, so `tools/screenshot.mjs` drives a
  real browser over every page and fails on a `.katex` node or a stray `**`.
  That is the only way this class of bug gets caught rather than shipped.

## Layout

```
app.py                 entry point, auth gate, navigation
debtapp/
  models.py            Debt / Payment / Profile, amortization formula
  engine.py            the simulation — the only place a month is defined
  payments.py          due-date calendar + the recorded-payment ledger
  insights.py          ranked, quantified advice
  charts.py            Plotly figures
  theme.py             design tokens: surfaces, type, palette, Plotly template
  security.py          password policy + recovery codes (no storage)
  db.py                persistence (SQLite or Postgres), bcrypt auth, sessions
  _pools.py            Postgres connection pools, one per DSN
  ui/common.py         the stylesheet + layout primitives every page builds from
  ui/plan.py           tiles, top suggestion, projection, one what-if lever
  ui/debts.py          focused account summaries and edit forms
  ui/ledger.py         due panel + recorded payments
  ui/onboarding.py     first run: one account, projected live
  ui/                  account settings and the auth screens
tests/                 engine, insight, and end-to-end UI tests
tools/screenshot.mjs   browser pass: screenshots + markup-leak check
```

## Disclaimer

A planning tool, not financial advice. Projections assume regular APRs stay
unchanged after any entered 0% promotional term, with no new borrowing and
monthly compounding. Deferred-interest offers are not modelled. If your minimum
payments are unaffordable, a nonprofit credit counselor (NFCC member agencies
are free) will help more than any calculator.
