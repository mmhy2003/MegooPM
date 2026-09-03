# Searching the list pages — design

## Goal

A search box on every list page, so an operator with sixty proxy hosts can find
one without reading the table.

Pages covered: Proxy Hosts, Upstream Pools, Certificates, Access Lists,
Streams, Redirection Hosts, 404 Hosts, Custom Pages, and Security's Decisions,
Alerts and Whitelists tabs. Security's Dashboard tab is excluded — it shows
counters, not a list.

## What the code already does

Eight of the list endpoints return a **plain array** — `list()` is
`api.get<ProxyHost[]>(BASE)` and its equivalents — so the browser already holds
every row. Filtering them is pure client-side work with no API change.

Security's **Whitelists** tab is the same: `list_whitelists` returns every row.

Security's **Decisions** and **Alerts** tabs are the exception. They paginate,
and that changes the answer.

### Why the paginated tabs cannot filter in the browser

A client-side filter on a paginated list searches **only the page on screen**.
An operator looking for an IP that sits on page 3 is told there are no matches.
That failure is silent, looks like an answer, and is worse than having no
search at all.

The fix is cheap because of how the backend already works: it fetches the
complete set from CrowdSec's LAPI and slices it **in memory**
(`paginate(items, page=…, page_size=…)` in `app/services/crowdsec/filtering.py`).
So a filter applied before that slice searches everything, and costs a few
lines.

## One component, one pure filter

A shared `SearchInput` — a labelled text input with a search icon and a clear
button — and one pure function:

```ts
filterBySearch<T>(items: T[], query: string, fields: (item: T) => string[]): T[]
```

`fields` returns strings, so a page with a numeric column — Streams' incoming
port — converts it there. Keeping the helper string-only means it never has to
guess how to render a number, a date or an enum for matching.

Each page supplies only its field list. That keeps eleven places' worth of
behaviour in **one tested function** instead of eleven hand-rolled `.filter()`
calls, which would drift in how they treat case, surrounding whitespace, and
array fields like `domain_names`.

Matching is a **case-insensitive substring**, and the query is trimmed. Not
fuzzy matching: an operator searching `api.example.com` wants that host, and a
fuzzy matcher returning six near-misses ranked by score is harder to trust than
one that either contains the text or does not.

### Which fields each page matches

Identifying fields, not every visible column. Matching every column sounds
generous but makes `active` match status badges and digits match dates, which
turns a specific search into a noisy one.

| page | fields |
| --- | --- |
| Proxy Hosts | domain names, forward host |
| Upstream Pools | name, backend hosts |
| Certificates | name, domain names |
| Access Lists | name |
| Streams | incoming port, forward host |
| Redirection Hosts | domain names, forward domain |
| 404 Hosts | domain names |
| Custom Pages | name, description |
| Whitelists | name, expressions |
| Decisions | IP/value, scenario |
| Alerts | source IP, scenario |

## The paginated tabs

`GET /crowdsec/decisions` and `/crowdsec/alerts` gain an optional `q`
parameter. The route filters the fetched list **before** calling `paginate`.

**`total` must be the filtered count.** It drives the pager, so returning the
unfiltered total offers pages that no longer exist — clicking page 3 of a
one-page result shows an empty table and looks like data loss.

The input **debounces at 300ms** and **resets to page 1** on every change.
Without the reset, filtering while on page 4 lands the operator past the end of
a shorter result set and shows nothing.

## Empty states name which kind of empty

"No hosts match `foo`" and "no hosts yet" are different statements, and a
filtered-empty table that reads like an empty install sends an operator looking
for a bug that is not there.

Every page therefore has two empty states, and the filtered one **offers a way
out** — a clear-search action — rather than leaving the operator to work out
that a stale filter is hiding their data.

## Error handling

There is nothing to fail client-side: filtering an array cannot throw, and an
unmatched query is a normal result, not an error.

For the paginated tabs, a search is an ordinary request on an existing
endpoint, so it fails the way that endpoint already fails and needs no new
handling.

## Testing

**The pure filter carries the risk**, and takes the coverage: case
insensitivity, a substring match rather than a prefix, matching inside an array
field, a query of only whitespace behaving as no query, an empty query
returning every row, and a no-match returning none.

**The backend** gets the two tests that describe the trap: `q` filters *before*
pagination — so a match on what would have been page 3 is found — and `total`
reflects the filtered count rather than the full one.

**Each view** gets the same three: typing narrows the list, clearing restores
it, and the filtered-empty message is distinguishable from the never-had-any
one.

**Not covered:** whether the field choices above are the ones an operator
reaches for. That is a judgement to revise in use, and revising it is editing a
list of field names.

## Files

**Frontend**

- `src/components/ui/search-input.tsx` (new)
- `src/lib/search.ts` (new) — `filterBySearch`
- the eight list views, plus Security's three tab panels (one file)
- tests alongside each

**Backend**

- `app/api/routes/crowdsec.py` — a `q` parameter on the two paginated routes
- `app/services/crowdsec/filtering.py` — the matching helper
- `backend/openapi.json` — regenerated

## Non-goals

- **Server-side search for the eight unpaginated pages.** The browser already
  has every row; adding a round trip per keystroke would make it slower.
- **Fuzzy or ranked matching.** See above.
- **Cross-page search.** A single box that searches hosts, certificates and
  streams at once is a different feature with a different UI.
- **Filters beyond text** — by status, by certificate, by node. Adjacent and
  legitimate, but a separate design.
- **Persisting the query.** The operator chose page state over a URL parameter,
  so a search does not survive navigation and is not linkable.

## Open risks

**Eleven places must adopt the same component.** The risk is not that one is
missed — that is visible — but that one is wired slightly differently and
behaves subtly unlike the rest. The shared function is what holds this
together; a page that hand-rolls its own `.filter()` re-opens exactly that gap.

**Very large lists filter on every keystroke.** With thousands of rows this
becomes visible on a slow machine. Real instances hold tens to low hundreds of
hosts, so this is worth knowing rather than solving now — and the fix, memoising
per query, is local to one function.
