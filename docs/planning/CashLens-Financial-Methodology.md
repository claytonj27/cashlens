# CashLens financial methodology

What every figure on the dashboard means, how it is produced, and what it is not. Written for
someone checking the numbers, not for someone reading the product.

Implemented in `services/api/app/analysis/` (`categorize.py`, `metrics.py`, `forecast.py`,
`routes.py`). Versions are returned with every response so a later reading can be matched to
the rules that produced it: `analysis-2`, `cash-2`, `rules-2`, `forecast-1`. Milestone 9
shipped `analysis-1`, `cash-1` and `rules-1`; what changed in milestone 10 is the counterparty
tier in section 4 and the owner corrections in section 4.1. The projection code is unchanged.

## 1. What is counted

**Money is integer minor units throughout**, signed, inflows positive. No figure is ever
derived from a formatted string, and no total is computed in the browser.

**Only posted transactions count.** A row must be `status = 'posted'` and have `removed_at`
null to enter any period total. Pending rows are counted and reported separately as
`pending_excluded`, never added in.

**Provenance is not a transaction.** A transaction linked to two statements through
`transaction_sources` is one transaction. The analysis reads `transactions`, never the
provenance table, so linking a CSV and a PDF of the same month cannot inflate a total.

**Nothing after the analysis date is read.** Every calculation filters on `posted_date <=
as_of` first. A historical analysis date therefore reports what was knowable then, which is what
makes a walkthrough of an earlier month honest.

## 2. Balances

Four kinds exist: `current`, `available`, `statement_opening`, `statement_closing`.

- A **current** balance with `bank_observed` provenance is live, and is the only kind the
  interface may present as what is in the bank now.
- A **statement closing** balance is history. It is shown as "the closing balance printed on
  your latest statement, not today's balance" and is flagged `is_live: false` in the response.
- **Available and current are never added together.** They describe the same money. The
  available figure is shown on its own, with a sentence saying why.
- One row per account per kind is kept, the newest on or before the analysis date. Where those
  rows carry different dates, the aggregate is labelled **latest known balances, mixed dates**
  rather than implying a single snapshot.

## 3. Monthly cash flow

Money in is the sum of positive posted amounts in the calendar month; money out is the sum of
the absolute value of negative ones; net is in minus out.

**Net cash flow is not profit.** It says what moved through the bank. It excludes anything
invoiced and unpaid, anything owed and unpaid, depreciation and every other accrual. The
dashboard says this in the card itself.

A month is **complete** only if it had ended by the analysis date. An incomplete month is
labelled a part month and the comparison card warns before comparing it with a full one.

### Internal transfers

Money moved between two of the owner's own accounts would otherwise appear as both income and
spending. A **matched pair** is two transfer-categorised rows in different accounts, equal in
size, opposite in sign, within three days. Both legs are removed from consolidated money in and
money out and reported as `transfers_netted_minor`, so total cash movement still shows them.

A transfer leg with **no match** is left in the totals. We cannot show that the money went
somewhere the owner still holds, so it is treated as ordinary spending.

## 4. Categories

Keyword rules on the description, applied in order, first match wins, published in full through
`GET /api/workspaces/{id}/analysis/rules` and readable in the dashboard.

A rule may be restricted to inflows or outflows, so "deposit" cannot categorise a payment out.
Matching is on word boundaries, so "rent" does not fire inside "current".

**Anything matching nothing stays `uncategorized`** and is shown as such, with a note that it
was left alone rather than guessed at. Growing the rule list is a deliberate change, not
something the system does on its own.

**The counterparty tier (`rules-2`).** Some descriptions name only how the money moved. Strip
the digits and the instrument words — `deposit`, `check`, `ach credit`, `ach debit`, `wire in`,
`wire out`, `zelle payment from`, `zelle payment to`, `venmo payment`, `mobile deposit`,
`check deposit`, `cash` — and if no word of three or more letters remains, the description names
nobody. Such a row is never placed by an instrument keyword: it stays `uncategorized` with the
reason `generic_instrument`. `ACH CREDIT 0192` names nobody; `ACH CREDIT HARBOR POINT HOA` names
Harbor Point and stays a customer receipt. The test is whether a counterparty survives, not
which word appeared, because the same word is generic on one row and specific on the next.

This is the promise in the paragraph above applied to the rows where `rules-1` broke it. Under
`rules-1` a bare `DEPOSIT 8842` was counted as a customer receipt, which is a guess about where
money came from; it is now left for a person to decide. In the demo businesses that moves
several thousand dollars of inflows out of customer receipts and into uncategorised. Each
placement carries a reason code — `keyword`, `generic_instrument` or `no_match` — published with
the rules.

`rules-2` also adds contractors, repairs and maintenance, and owner contributions as categories,
and matches `equipment lease` before `equipment`, so financing a machine reads as rent rather
than a purchase and a service call on one reads as maintenance.

### 4.1 Corrections by the owner

A person can set the category of a transaction themselves. A correction is stored as an
append-only row scoped to one workspace, recording who decided, when, and the transaction
revision it was decided against; the newest row for a transaction wins and the earlier ones are
kept. The analysis applies current corrections when it reads, so the rules remain the only thing
that placed anything and the correction stays visibly the owner's own.

A correction moves exactly that transaction's amount between category totals on the next read.
It changes no rule, no prompt and no model, and it is never applied to another workspace, so the
interface says the category was **set by you** and **saved**, never that anything was learned.

**Where a correction shows up, and where it deliberately does not.** A correction is read on
every analysis read, so the grouping card, the month comparison and transfer pairing all follow
it immediately: those are computed per transaction, and exactly one transaction moved.

The **recurring payments card is different on purpose**. A recurring item is a group of rows
sharing a description, and it reports the category most of those rows are in. Correcting one
row out of twelve therefore moves the grouping card straight away while the recurring card
still shows the old category, because eleven rows still carry it. That is not a lag or a cache:
it is the card answering the question it exists to answer, which is what this repeating payment
usually is. Correct the rest of the group and the recurring card follows.

The projection is unchanged code (`forecast-1`), but its inputs move with the monthly totals,
which is the point of making a correction in the first place.

**Suggestions change nothing.** A proposed category is stored and shown; no figure moves until
a person accepts it, picks a different one, or leaves it alone. An accepted or chosen category
records who decided, when, what the category was before, and which suggestion it answered.

Three activity groups:

| Group | Contains | Why it is separate |
| --- | --- | --- |
| Running the business | receipts, payroll, suppliers, rent, utilities, insurance, software, marketing, bank fees, tax, equipment, and anything uncategorised | the ordinary trading of the business |
| Loans and owner money | loans, credit lines, working-capital advances, owner draws | cash that moves without being trade, so it should not read as earnings |
| Between your own accounts | matched internal transfers | not income or spending at all |

**These are cash groupings for reading a statement. They are not confirmed accounting
classifications**, and the interface says so wherever they appear. Equipment is shown as its own
category inside operating rather than being treated as capital expenditure, because this
milestone reports cash and does not keep a balance sheet.

## 5. Recurring payments

A description qualifies when it has appeared at least three times with a steady gap: the median
gap between two and forty-five days, and no single gap deviating from the median by more than
the larger of seven days and a third of the median.

The reported amount and cadence are **means**, not medians. A median amount multiplied by a
median cadence drifts away from what the months actually show; the mean of both reproduces the
observed monthly contribution closely.

The next date is the last occurrence plus the usual gap. It is **an estimate from a pattern, not
a bill anyone has told us about**, and is labelled that way.

## 6. The 30-day projection

Deterministic: the same inputs give the same path every time. No model is called.

1. **Anchor.** The chosen balance from section 2, on or before the analysis date. A live balance
   wins; a statement closing balance is used only if there is no live one and is never described
   as current.
2. **Recurring events.** Each recurring item from section 5 is placed on its next expected dates
   across the window.
3. **Everyday rate.** From complete months only, the last three at most: total money in and
   total money out per day, with the recurring items' contribution subtracted first so placing
   them on dates does not count them twice. Inflow and outflow are kept apart so a change to
   customer receipts scales what actually arrives.

**It refuses rather than guessing.** With fewer than two complete months, no posted
transactions, no dated balance, or a newest transaction more than forty-five days before the
analysis date, the forecast returns `available: false` with the reasons listed, and the
dashboard shows them instead of a chart.

Every future point is an estimate and is labelled as one. The assumptions list is returned with
the result and rendered in full.

## 7. Scenarios

Three controls, all optional, all applied to the projection only:

- **Customer receipts change by a percentage.** Scales recurring inflows and the everyday
  inflow rate across the window.
- **A payment delayed by a number of days.** The owner supplies the amount and the date they
  expect it. **Bank history is not evidence of an unpaid invoice**, so nothing is inferred; the
  assumption text says the figure is the owner's.
- **A one-time expense** on a date, optionally named.

Results report ending cash, the lowest projected balance and its date, the difference from
baseline, and **the difference in the low point**, which is usually the number that matters for
a delay: money that still arrives inside the window leaves ending cash unchanged while making
the dip worse.

**A scenario never writes anything.** It is computed per request, for one workspace, from the
same read-only inputs as the baseline. Transactions, balances and imports are untouched, which
is asserted by test and by the development check.

## 8. Freshness and evidence

Every request reads the database directly. A confirmed import therefore changes the dashboard on
the next load with no reseed and no cache to invalidate. The development check proves this by
confirming an import and asserting the answer changes.

Category rows carry the transaction ids behind them (capped at 200 per row) so the interface can
link a total to the transactions that make it up, using the transaction and evidence views that
already exist.

## 9. What this milestone does not do

- No accrual accounting, no profit, no balance sheet, no tax treatment.
- No confidence intervals. A scenario is an assumption, not a probability.
- No learned or model-assisted categorisation. That is milestone 10, which will explain these
  calculated results rather than replace them.
- No multi-currency. USD only, enforced upstream.
- Equipment is reported as a cash outflow in its own category, not capitalised.
- The everyday rate is flat. It does not model seasonality, day-of-week effects or growth.
