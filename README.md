# yclass

Home Assistant integration for schools in Göteborgs Stad that use
[Vklass](https://www.vklass.se). For each student it shows upcoming homework
and tests, when the school day starts and ends, and what's for lunch. It also
includes a dashboard card that shows all of it together.

It signs in the same way a student does in a browser (Vklass → *Inloggning för
personal och elever* → *Logga in med Göteborgs Stad*) and reads *Kalender och
schema* and the lunch menu on the Vklass start page.

## What you get, per student

Each student is a config entry and a device named after them in Vklass.

| Entity | What it holds |
| --- | --- |
| `sensor.<name>_skoldag` | When the coming school day starts (timestamp). Until 17:00 that is today, after 17:00 the next day with lessons. Attributes: `first_name`, `date`, `start`, `end`, `lunch` (that day's dishes), `reference_date`, `days` (the next 10 school days with start, end and lunch). |
| `sensor.<name>_kommande_laxor` | Number of homework/tests due today or later. Attributes: `homework` (the list), `next_title`, `next_kind`, `next_subject`, `next_due`, `next_description`. |
| `calendar.<name>_laxor` | Homework and tests as all-day events, titled `<title> (<subject>)`, with kind, due time, the teacher's text and a link to Vklass. |
| `calendar.<name>_lediga_dagar` | Lov and other days off. |

(Entity ids follow HA's language when the entry is added; the ones above are
for Swedish.)

The school day is taken from the first and last lesson that isn't cancelled.
Vklass is checked once an hour, covering 30 days back and 90 days ahead.
Calendar views outside that range are fetched when you open them. The lunch
menu covers the current and the next week, which is as far as Vklass
publishes it.

## Install with HACS

1. HACS → ⋮ (top right) → *Custom repositories*. Repository
   `https://github.com/henriklewander/yclass`, type *Integration* → *Add*.
2. Search HACS for **yclass** → *Download*, then restart Home Assistant.
3. *Settings → Devices & services → Add integration → yclass*. Enter the
   student's Göteborgs Stad username and password. Repeat for each child.

HACS tells you when there is a new release; update there and restart.

If a password changes, HA asks for the new one under *Settings → Devices &
services* (re-authentication). Entering it there is enough.

Without HACS: copy `custom_components/yclass` to
`/config/custom_components/yclass` and restart.

## The card

The integration serves the card itself and loads it on every dashboard, so
there is no dashboard resource to add. On a dashboard: *Edit → Add card*,
then either tab *By card* (*Efter kort*) and search for **yclass**, or tab *By
entity* (*Efter entitet*), search for the student's name, pick one of their
entities and choose the suggested **yclass – elev**. In YAML, one card per
student:

```yaml
type: custom:yclass-card
device: <the student's device id>   # chosen with the picker in the visual editor
max_items: 10                       # optional, default 10
```

It shows the student's first name, *Idag*/*Imorgon* with the start and end
time, that day's lunch, and the homework and tests from that day on, in order
and with the day they are due. Tests get a red *Prov* label. Tap an item to see
the teacher's text and a link to Vklass. After 17:00 the card switches to the
next school day: times, lunch and the list all move on.

## Markdown alternative

The same data works in a plain Markdown card, for example the next two weeks:

```yaml
type: markdown
title: Läxor
content: |
  {% set hw = state_attr('sensor.<name>_kommande_laxor', 'homework') or [] %}
  {% set until = (now() + timedelta(days=14)).date() | string %}
  {% set days = ['mån', 'tis', 'ons', 'tor', 'fre', 'lör', 'sön'] %}
  {% for h in hw if h.due[:10] <= until %}
  {% set d = as_datetime(h.due) %}
  - **{{ days[d.weekday()] }} {{ d.day }}/{{ d.month }}** – {{ h.title }}
    {%- if h.subject %} · *{{ h.subject }}*{% endif %}
    {%- if h.kind and h.kind != 'Läxa' %} · **{{ h.kind }}**{% endif %}
  {% else %}
  Inga läxor de närmaste två veckorna 🎉
  {% endfor %}
```

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env && chmod 600 .env        # fill in VK_USER / VK_PASS
.venv/bin/python scripts/yclass_cli.py         # sign in, print the next 21 days
.venv/bin/python scripts/yclass_cli.py --env .env.other   # another student
.venv/bin/python -m pytest -q tests            # offline parser tests
```

`custom_components/yclass/vklass.py` is the whole Vklass client and has no
Home Assistant imports; the CLI uses it directly.

### How the sign-in works

1. `GET auth.vklass.se/saml/initiate?idp=https://auth.goteborg.se/idp/sps/skola/saml20&org=189`
   redirects to an IBM ISAM login form on `auth.goteborg.se`.
2. The username and password are posted to that form, which answers with an
   auto-submitting SAML form back to `auth.vklass.se/saml/assertion`.
3. After that the `.vklass.se` cookies are the session.
   `GET app.vklass.se/Events/FullCalendar?start=…&end=…` returns the calendar
   as JSON; assignments are `eventType` 2, days off 8.
4. The kind and subject ("Läxa, Matematik") are only on the assignment's detail
   page, so each new assignment costs one extra request (cached afterwards).
5. The lunch menu is JSON in the `school-lunch-items.bind` attribute of
   `app.vklass.se/Home/Welcome`.

`/Events/FullCalendar` stops about a month after `start` without saying so, so
longer ranges are fetched in 28-day slices.

Two aiohttp details matter, both handled in `vklass.py`:

- Redirects are followed by hand with `yarl.URL(..., encoded=True)`. aiohttp's
  own redirect handling re-quotes `%2F` in the signed `SAMLRequest`, and the
  IdP rejects it (`FBTSML238E`).
- The cookie jar is created with `quote_cookie=False`. `GBG-SESSION-ID`
  contains `=`, and a quoted value sent back is not recognised as a session,
  so the login form just reappears.

### The card and HA's frontend boot

The card is loaded with `add_extra_js_url`, which runs early in the frontend's
boot. HA 2026.10 replaces `window.customElements` with its own registry during
boot. An element defined before that is invisible to dashboards ("Custom
element doesn't exist"), so the card waits until `<home-assistant>` is defined
in `window.customElements` before it defines itself.

## Versions

`version` in `custom_components/yclass/manifest.json` is the release. Every
change bumps it (0.5.0, 0.5.1, …) and gets a matching `vX.Y.Z` tag and GitHub
release, which is what HACS offers as an update. See [CHANGELOG.md](CHANGELOG.md).
