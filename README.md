# SFMTA Special Event Parking → ICS

SFMTA charges elevated parking meter rates near Oracle Park and Chase Center
during large events, and publishes the schedule as an HTML table on a notice
page. This repository scrapes that page once a day and republishes it as a
subscribable calendar feed on GitHub Pages.

**Source:** <https://www.sfmta.com/notices/current-special-event-parking-regulations-schedule>

**Feed:** `https://nickfong.github.io/mbevents/sfmta-events.ics`

The events are **meter enforcement windows, not event start times**. Every
event's summary and description says so.

---

## How it works

```
fetch.py      GET the notice page. One retry on a transient network error, then fail.
extract.py    BeautifulSoup. Pull out the Effective Date and the schedule table.
              Only the table's text goes any further -- never the whole page.
parse.py      Claude Code CLI, headless. Transcribes the table text to JSON.
              This is the only place a model is involved, and it does nothing else.
validate.py   Year inference, venue whitelist, time whitelist, sanity windows.
              All interpretation happens here, in testable Python.
build.py      Render RFC 5545 with a real VTIMEZONE.
state.py      SEQUENCE bookkeeping, so clients apply revisions in place.
main.py       Orders the above. Writes nothing until everything has validated.
```

The model's job is deliberately mechanical: copy the table into JSON. It does
not infer years, interpret times, or decide anything. If its output is not
valid JSON, the run fails and prints the raw output verbatim — there is no
fence-stripping, no regex repair, no silent retry, and no fallback parser.

### Design rule: no silent failure

This job runs unattended for years. A stale-but-quiet calendar is worse than a
job that stops and emails you. Everything below exits 1; nothing is logged as a
warning and skipped:

- the fetch fails, times out, or returns non-200
- the schedule table cannot be found in the HTML
- the table has zero data rows
- the parse step returns anything that is not valid JSON in the expected shape
- any row's date does not parse
- any venue is not exactly `Oracle` or `Chase`
- any hours string is not in the whitelist
- the year inference is ambiguous
- the row count fell by more than 40% versus the last successful run
- the OAuth token is missing or rejected (this one gets its own message)

**Nothing is written on failure.** The calendar is built in memory, re-parsed by
`icalendar`, and only then written to disk. A failed run leaves the last good
`docs/sfmta-events.ics` and `state/events.json` exactly as they were, so
subscribers keep working data while you fix it.

### Year inference

The table's dates have no year. So:

1. Read the page's `Effective Date` to get an anchor year.
2. Walk the rows in order, starting at that year.
3. When the month index decreases (December → January), increment the year.
4. If that happens **more than once**, the schedule would span over twelve
   months. It never does. Exit 1.
5. Every resulting date must land within `effective − 30 days` to
   `effective + 400 days`. Otherwise the inference broke. Exit 1.

### Time whitelist

| Source string | Start | End |
|---|---|---|
| `Noon to 6 P.M.` | 12:00 | 18:00 |
| `3 P.M. to 10 P.M.` | 15:00 | 22:00 |
| `6 P.M. to 10 P.M.` | 18:00 | 22:00 |

Whitespace and case are normalised before matching; nothing else is. A new
SFMTA window turns the build red so you add it deliberately, in
`HOURS_WINDOWS` in `sfmta_ics/config.py`.

### SEQUENCE and revisions

`state/events.json` records the start, end and `SEQUENCE` each UID was last
published with. On each run a UID whose times changed gets its sequence
incremented; unchanged events keep theirs; new events start at 0. That is what
makes a calendar client update an event in place when SFMTA revises the hours
rather than ignoring the change.

UIDs are `{YYYYMMDD}-{venue}@{pages-hostname}`, e.g.
`20260710-oracle@nickfong.github.io`. Stable across runs, so subscribers never
accumulate duplicates. The same date can legitimately appear twice with
different venues, which is why the venue is in the key.

Events that disappear from the page simply do not appear in the republished
file, and their state entries are pruned.

### The 60-day kill switch

GitHub disables scheduled workflows after 60 days with no repository activity.
**Workflow runs do not count as activity — only commits do.** A daily cron that
commits nothing gets switched off on day 61, which in the baseball offseason is
entirely plausible.

So every successful run writes `state/last-run.txt` and commits it
unconditionally, whether or not the calendar changed. That resets the timer and
doubles as a visible last-good-run marker in the commit history.

(In practice the ICS itself also changes daily, because `X-WR-CALDESC` carries
the last-scrape timestamp so you can see staleness from inside your calendar
app. The heartbeat is the guarantee; the ICS churn is a side effect.)

---

## Setup — do these in order

1. **Create the repository, public.**

   GitHub Pages needs a paid plan to serve from a private repo, the published
   site is public either way, and public repos get free unmetered Actions
   minutes. There is nothing sensitive in this tree.

   Push this code to the default branch.

2. **Enable GitHub Pages on `docs/`.**

   Settings → Pages → Build and deployment → Source: **Deploy from a branch** →
   Branch: **`master`**, folder: **`/docs`**
   → Save.

3. **Generate an OAuth token.** On your own machine, signed in to Claude Code:

   ```
   claude setup-token
   ```

   Copy the token it prints. This uses your Claude Max subscription. It is a
   token, not an API key, and it has a finite lifetime.

4. **Add it as a repository secret.**

   Settings → Secrets and variables → Actions → New repository secret

   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: the token from step 3

   Do **not** set `ANTHROPIC_API_KEY`. If it is present anywhere in the
   environment it takes precedence over the OAuth token and bills a separate
   account silently. The workflow `unset`s it, and `parse.py` strips it from
   the subprocess environment as well.

5. **Trigger the first run by hand.**

   Actions → *Update SFMTA parking calendar* → **Run workflow**.

   This proves the token works and produces the first
   `docs/sfmta-events.ics`. After that it runs daily at 13:10 UTC.

6. **Subscribe.**

   ```
   https://<your-username>.github.io/<repo-name>/sfmta-events.ics
   ```

   For `nickfong/mbevents`:

   ```
   https://nickfong.github.io/mbevents/sfmta-events.ics
   ```

   Add it as a **subscribed / internet calendar**, not a one-time import — an
   import will not pick up revisions.

   - **Google Calendar:** Other calendars → + → From URL
   - **Apple Calendar:** File → New Calendar Subscription
   - **Outlook:** Add calendar → Subscribe from web

   The feed asks clients to refresh every 12 hours. Google in particular
   ignores this and refreshes on its own schedule, sometimes only every day or
   two. That is a client-side limit, not a feed problem.

   Pages can take a minute or two to publish after the first run.

If you fork this, set the `PAGES_HOSTNAME` environment variable (or edit
`config.py`) so your UIDs live in your own namespace rather than inheriting
someone else's.

---

## What the failure emails mean

GitHub emails you when the workflow fails. The first line of stderr tells you
which of these it was.

| Message starts with | What happened | What to do |
|---|---|---|
| `FATAL: Claude Code authentication failed` | The OAuth token expired or was invalidated — e.g. you logged out of Claude Code somewhere. | Re-run `claude setup-token`, update the `CLAUDE_CODE_OAUTH_TOKEN` secret, then re-run the workflow manually. The message spells this out in full. |
| `FATAL: Could not fetch ...` | Two consecutive network failures reaching sfmta.com. | Usually transient — check the page in a browser and re-run. If it persists, SFMTA is down or blocking the runner. |
| `FATAL: Fetching ... returned HTTP 404` | The notice page moved, was renamed, or was withdrawn. | Find the new URL and update `SOURCE_URL` in `config.py`. |
| `FATAL: Could not locate an 'Effective Date'` | The page layout changed. The year inference has no anchor. | Look at the page, adjust `extract.py`, add a fresh fixture. |
| `FATAL: ... none of which has both an 'Event Date' and a 'Venue' column` | The schedule table is gone or its headings were renamed. | Same as above. |
| `FATAL: The parse step did not return valid JSON` | The model returned prose, fences, or something malformed. The raw output is printed in full. | Usually a re-run fixes it. If it repeats, tighten the prompt in `parse.py`. |
| `FATAL: Row N: rate hours '...' are not in the known whitelist` | SFMTA introduced a new enforcement window. | Add it to `HOURS_WINDOWS` in `config.py`, with a test. |
| `FATAL: Row N: venue '...' is not one of ['Chase', 'Oracle']` | A third venue appeared. | Decide whether you want it, then add it to `VENUES`. |
| `FATAL: Row N: the month index decreased for a second time` | The table is no longer a single sub-twelve-month schedule in date order. | Look at the page; the year inference is not trustworthy until you understand why. |
| `FATAL: Row N: inferred date ... falls outside the sane window` | The effective date and the rows disagree badly. | Same. |
| `FATAL: Row count fell from X to Y` | The row count collapsed by more than 40%. Nearly always a broken extraction rather than a genuinely short schedule. | Check the page. If the schedule really did shrink that much, lower `row_count` in `state/events.json` and re-run — re-running alone will keep hitting this, because the state file is only updated on success. |
| `FATAL: state/events.json exists but is not valid JSON` | The state file got corrupted. | Fix it, or delete it deliberately. Note that deleting it resets every `SEQUENCE` to 0, so in-flight revisions may not reach subscribers. |

In every case the previously published calendar is untouched. Subscribers keep
the last known good data until you fix the cause.

---

## Development

```bash
pip install -r requirements.txt
pytest
```

The tests never touch the network and never invoke the model. `fetch_page` and
`parse_rows` are stubbed; the parser tests run against a snapshot of the notice
page in `tests/fixtures/`.

They cover: table extraction including the trailing blank row and the duplicate
date/venue pairs; year inference including a December→January rollover and a
double-rollover that must fail; the time whitelist including an unknown string
that must raise; the SEQUENCE rules; a zero-row parse that must raise rather
than produce an empty calendar; and a full ICS round-trip through `icalendar`.

### Refreshing the fixture

`tests/fixtures/notice-page.html` is a reduced stand-in for the live page: the
`Effective Date` block, the schedule table, and one unrelated table to prove the
extractor selects by column heading rather than by position. To replace it with
a genuine snapshot:

```bash
curl -sS -A "Mozilla/5.0" \
  https://www.sfmta.com/notices/current-special-event-parking-regulations-schedule \
  -o tests/fixtures/notice-page.html
```

Then update the row-count and boundary assertions in `tests/test_extract.py` and
`tests/test_validate.py`, and regenerate `tests/fixtures/parsed-rows.json` to
match. The extractors match on visible text rather than on Drupal CSS classes,
so a real snapshot should drop straight in.
