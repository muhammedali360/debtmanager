# Debt Manager

A Streamlit app for people carrying debt: enter your cards and loans, see exactly
where the money is going, and find out what it would actually take to get out.

## What it does

- **Two debt shapes, modelled properly.** Credit cards use a percent-of-balance
  minimum with a dollar floor — so the minimum *shrinks as you pay*, which is the
  trap. Term loans use a fixed contractual payment.
- **A real amortization engine.** One code path (`debtapp/engine.py`) produces
  every number in the app, so the dashboard, insights, and scenarios can never
  disagree. Validated against closed-form amortization tables.
- **Visualizations**: balance projection, cumulative principal vs interest, the
  cents-on-the-dollar split, interest by year, payoff timeline, per-account
  interest, and a strategy bake-off.
- **~20 quantified insights**, ranked by dollars at stake — balance transfers net
  of fees, consolidation, utilization and its credit-score cost, debt-to-income,
  biweekly payments, windfall timing, the cost of waiting six months.
- **What-if sandbox**: extra payments, lump sums, annual raises, and a solver
  that works backwards from a target debt-free date to the payment it requires.
- **Accounts and persistence** so you can come back and pick up where you left
  off, plus a progress chart across check-ins.

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
realistic portfolio you can edit.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

129 tests: engine math against closed-form amortization answers, insight
correctness, the full auth surface (policy, throttling, session expiry,
recovery codes), and end-to-end UI tests that drive every page and the login
screen through Streamlit's `AppTest` — including empty accounts, zero-APR
loans, and negative-amortization plans.

## Persistence and deployment

Data lives in SQLite at `data/debt.db`. Override with the `DEBTMANAGER_DB`
environment variable.

> **Deploying to Streamlit Community Cloud:** the container filesystem is
> **ephemeral** — it is wiped on every reboot and redeploy, so `data/debt.db`
> will not survive. Point `DEBTMANAGER_DB` at a mounted volume, or switch
> `debtapp/db.py` to a hosted Postgres (it is the only module that touches
> storage). Everything else works unchanged.

## Design notes

- **Money math** is monthly-compounded and rounded to the cent each month, the
  way a servicer actually posts. A 60-month loan can therefore end with a
  sub-dollar 61st stub payment — that is correct, not a bug.
- **Insights never quote a savings figure against a plan that never terminates.**
  If minimum payments would never clear the debt, the app says so and reports
  what you'd have paid and still owe, rather than inventing a number from
  wherever the simulation stopped.
- **Charts** follow a validated palette (colorblind-safe, checked in both light
  and dark against their own surfaces). Every chart ships a table view, a legend
  for two or more series, and selective direct labels rather than a number on
  every point.

## Layout

```
app.py                 entry point, auth gate, navigation
debtapp/
  models.py            Debt / Profile, amortization formula
  engine.py            the simulation — the only place a month is defined
  insights.py          ranked, quantified advice
  charts.py            Plotly figures
  theme.py             validated palette + Plotly template
  security.py          password policy + recovery codes (no storage)
  db.py                SQLite persistence, bcrypt auth, sessions, throttling
  ui/                  one module per page
tests/                 engine, insight, and end-to-end UI tests
```

## Disclaimer

A planning tool, not financial advice. Projections assume unchanging APRs, no
new borrowing, and monthly compounding. If your minimum payments are
unaffordable, a nonprofit credit counselor (NFCC member agencies are free) will
help more than any calculator.
