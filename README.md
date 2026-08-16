# SFMTA Special Event Parking → ICS

SFMTA charges elevated parking meter rates near Oracle Park and Chase Center
during large events, and publishes the schedule as an HTML table on a notice
page. This scrapes that page daily and republishes it as a calendar feed on
GitHub Pages.

Source: <https://www.sfmta.com/notices/current-special-event-parking-regulations-schedule>

Feed: `https://nickfong.github.io/MBEvents/sfmta-events.ics`

The times are meter enforcement windows, not event start times.

## How it works

| | |
|---|---|
| `fetch.py` | GET the notice page. One retry on a transient network error. |
| `extract.py` | Pull out the Effective Date and the schedule table. Only the table text goes further, not the whole page. |
| `parse.py` | Claude Code CLI, headless. Transcribes the table text to JSON. |
| `validate.py` | Year inference, venue and time whitelists, sanity windows. |
| `build.py` | Render RFC 5545 with a real VTIMEZONE. |
| `state.py` | SEQUENCE bookkeeping and the past-event archive. |
| `main.py` | Orders the above. |

The model only transcribes the table. It does not infer years, interpret times,
or decide anything. Output that is not valid JSON fails the run and prints the
raw response; there is no fence-stripping, regex repair, silent retry, or
fallback parser.

### Failure behaviour

All of these exit 1. None is logged as a warning and skipped:

- the fetch fails, times out, or returns non-200
- the schedule table cannot be found
- the table has zero data rows
- the parse step returns anything other than valid JSON in the expected shape
- a non-blank date does not parse
- a non-blank venue is not exactly `Oracle` or `Chase`
- a non-blank hours string is not in the whitelist
- more than 3 rows have blank cells
- the year inference is ambiguous
- the row count fell by more than 40% since the last successful run
- the OAuth token is missing or rejected

Nothing is written on failure. The calendar is built in memory, re-parsed by
`icalendar`, and only then written to disk, so a failed run leaves the last good
`docs/sfmta-events.ics` and `state/events.json` in place.

### Blank cells: quarantine

SFMTA does occasionally publish a row with an empty cell (it happened on
2026-08-16: a September date with blank hours). One typo'd cell should not stop
the other 70+ rows from publishing, but it must never be silent either. So:

- A row with a **blank** cell is quarantined. If the date and venue still name
  an event published previously (and it is still in the future), it is carried
  forward frozen at its last known values, so the blank does not read as a
  cancellation. Otherwise the row is left out of this publish and appears once
  SFMTA fixes the cell.
- The run then exits **2**: the workflow commits the calendar first and fails
  afterwards, so the feed updates *and* the failure email goes out. This
  repeats daily until the cell is fixed or the date passes.
- More than 3 blank rows is still exit 1 with nothing written — that is a
  broken table, not a typo.
- Only blanks are quarantined. A cell that is present but invalid (an unknown
  hours window, a third venue) still fails the run, because it needs a
  deliberate whitelist decision.

### Year inference

The table's dates have no year.

1. Read the page's `Effective Date` for an anchor year.
2. Walk the rows in order from that year.
3. Increment the year when the month index decreases (December → January).
4. More than one increment exits 1. A schedule spanning over twelve months means
   the page changed shape.
5. Every date must land within `effective − 30 days` to `effective + 400 days`.

### Time whitelist

| Source string | Start | End |
|---|---|---|
| `Noon to 6 P.M.` | 12:00 | 18:00 |
| `3 P.M. to 10 P.M.` | 15:00 | 22:00 |
| `6 P.M. to 10 P.M.` | 18:00 | 22:00 |

Whitespace and case are normalised before matching, nothing else. A new SFMTA
window turns the build red; add it to `HOURS_WINDOWS` in `sfmta_ics/config.py`.

### SEQUENCE

`state/events.json` records the start, end, venue, hours, `SEQUENCE` and
`LAST-MODIFIED` each UID was last published with. A UID whose times changed gets
its sequence incremented, unchanged events keep theirs, new events start at 0.
Without this, clients ignore revised hours.

UIDs are `{YYYYMMDD}-{venue}@{pages-hostname}`, e.g.
`20260710-oracle@nickfong.github.io`. The venue is part of the key because the
same date appears twice when both venues have events.

### The archive

SFMTA publishes a rolling window, so finished dates fall off the page. Building
the ICS purely from the page would delete that history out of subscribers'
calendars as the window advances. So a UID that has left the page is handled by
its date:

- **past** — archived. Re-emitted from the state file forever, frozen at its
  last published values. A retroactive SFMTA edit to a finished date cannot
  rewrite history, because archived events are never re-derived from the page.
- **future** — dropped. That is a cancellation or a reschedule, and it should
  leave the calendar.

"Past" means strictly before today in `America/Los_Angeles`. Today's events
still mirror the page.

The archive is unbounded. Growth is roughly 100 KB/year at ~150 events; worth
revisiting around 1 MB, since clients re-download the whole file each refresh.

One interaction to know about: the 40% row-count guard compares **scraped** rows
only, never the published total, so a growing archive can never mask a scrape
that collapsed. The trade-off is that a republication dropping more than 40% of
the rows halts the run even when the loss is legitimate rolloff. The guard
cannot tell rolloff from a broken extraction, and the conservative choice is to
stop. Recovery is the manual `row_count` edit described in the failure table.

### Heartbeat

GitHub disables scheduled workflows after 60 days without repository activity,
and workflow runs do not count as activity — only commits do. Every successful
run writes and commits `state/last-run.txt` whether or not the calendar changed.

The ICS also changes daily regardless, since `X-WR-CALDESC` carries the
last-scrape timestamp so staleness is visible from inside a calendar app.

## Setup

1. Repository must be public. Pages needs a paid plan to serve from a private
   repo, and public repos get free unmetered Actions minutes.

2. Settings → Pages → Deploy from a branch → `master`, folder `/docs`.

3. Generate a token on a machine signed in to Claude Code:

   ```
   claude setup-token
   ```

4. Settings → Secrets and variables → Actions → new secret
   `CLAUDE_CODE_OAUTH_TOKEN`.

   Do not set `ANTHROPIC_API_KEY`. It takes precedence over the OAuth token and
   bills a different account. The workflow `unset`s it and `parse.py` strips it
   from the subprocess environment.

5. Actions → Update SFMTA parking calendar → Run workflow. This produces the
   first `docs/sfmta-events.ics`. After that it runs daily at 13:10 UTC.

6. Subscribe to:

   ```
   https://nickfong.github.io/MBEvents/sfmta-events.ics
   ```

   Add it as a subscribed calendar, not a one-time import, or revisions will not
   arrive. Google Calendar: Other calendars → + → From URL. Apple Calendar: File
   → New Calendar Subscription. Outlook: Add calendar → Subscribe from web.

   The feed asks for a 12-hour refresh. Google ignores this and refreshes on its
   own schedule, sometimes only every day or two.

Forks should set `PAGES_HOSTNAME` so UIDs land in their own namespace.

## Failure modes

The workflow emails on failure. The first line of stderr says which of these it
was.

| Message | Cause | Fix |
|---|---|---|
| `Claude Code authentication failed` | Token expired or invalidated, e.g. by logging out of Claude Code elsewhere. | Re-run `claude setup-token`, update the secret, re-run the workflow. |
| `Could not fetch ...` | Two consecutive network failures reaching sfmta.com. | Usually transient. Re-run. |
| `Fetching ... returned HTTP 404` | Notice page moved or withdrawn. | Update `SOURCE_URL` in `config.py`. |
| `Could not locate an 'Effective Date'` | Page layout changed; year inference has no anchor. | Adjust `extract.py`, refresh the fixture. |
| `none of which has both an 'Event Date' and a 'Venue' column` | Schedule table gone or headings renamed. | Same. |
| `The parse step did not return valid JSON` | Model returned prose or fences. Raw output is printed. | Re-run. If it repeats, tighten the prompt in `parse.py`. |
| `rate hours '...' are not in the known whitelist` | New enforcement window. | Add it to `HOURS_WINDOWS`, with a test. |
| `WARNING: ... rows ... have blank cells` (run red, calendar still updated) | SFMTA published a row with an empty cell. The rest published; the row was carried forward or left out, as the log says. | Nothing urgent. The email repeats daily until SFMTA fixes the cell or the date passes. |
| `... rows have blank cells (quarantine limit is 3)` | Most of the table is blank — broken table or broken extraction. | Check the page; nothing was published. |
| `venue '...' is not one of ['Chase', 'Oracle']` | Third venue appeared. | Add it to `VENUES` if wanted. |
| `the month index decreased for a second time` | Table is no longer a single sub-twelve-month schedule in date order. | Check the page before trusting the inference. |
| `inferred date ... falls outside the sane window` | Effective date and rows disagree. | Same. |
| `Row count fell from X to Y` | Scraped rows dropped over 40%. Usually a broken extraction, but a large legitimate rolloff can do it too. | If the schedule really shrank, lower `row_count` in `state/events.json` and re-run. The state file only updates on success, so re-running alone will keep failing. |
| `state/events.json exists but is not valid JSON` | Corrupt state. | Fix or delete it. Deleting resets every `SEQUENCE` to 0, so in-flight revisions may not reach subscribers. |

The published calendar is untouched in every case.

## Development

```bash
pip install -r requirements.txt
pytest
```

Tests do not touch the network or invoke the model. `fetch_page` and
`parse_rows` are stubbed; parser tests run against a fixture of the notice page.

Coverage: table extraction including the trailing blank row and duplicate
date/venue pairs; year inference including a December→January rollover and a
double rollover that must fail; the time whitelist including an unknown string;
the SEQUENCE rules; a zero-row parse that must raise; and an ICS round-trip
through `icalendar`.

### Fixture

`tests/fixtures/notice-page.html` is a reduced stand-in: the Effective Date
block, the schedule table, and one unrelated table to prove the extractor
selects by column heading rather than position. To replace it with a real
snapshot:

```bash
curl -sS -A "Mozilla/5.0" \
  https://www.sfmta.com/notices/current-special-event-parking-regulations-schedule \
  -o tests/fixtures/notice-page.html
```

Then update the row-count and boundary assertions in `test_extract.py` and
`test_validate.py`, and regenerate `tests/fixtures/parsed-rows.json`. The
extractors match on visible text rather than Drupal CSS classes, so a real
snapshot should drop in.
