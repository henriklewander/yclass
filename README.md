# yclass

Home Assistant integration for schools in Göteborgs Stad that use
[Vklass](https://www.vklass.se). For each student it shows upcoming homework
and tests, when the school day starts and ends, and what's for lunch. It also
comes with a dashboard card that shows all of it together.

## Install with HACS

1. HACS → ⋮ (top right) → *Custom repositories*. Repository
   `https://github.com/henriklewander/yclass`, type *Integration* → *Add*.
2. Search HACS for **yclass** → *Download*, then restart Home Assistant.
3. *Settings → Devices & services → Add integration → yclass*. Enter the
   student's Göteborgs Stad username and password (the login used for
   *Inloggning för personal och elever* in Vklass). Repeat for each child.

If a password changes, HA asks for the new one under *Settings → Devices &
services*.

## The card

*Edit dashboard → Add card*, then search for **yclass** under *By card*, or
pick one of the student's entities under *By entity* and choose the suggested
**yclass – elev**. Add one card per student.

The card shows the student's first name, when the school day starts and ends,
that day's lunch, and homework and tests in date order. Tests are marked
*Prov*, and a day with Idrott och hälsa gets an *Idrott* row. Tap an item to
read the teacher's text. After 17:00 the card shows the next school day.

```yaml
type: custom:yclass-card
device: <the student's device>   # picked in the visual editor
max_items: 10                    # optional
```

## Entities, per student

| Entity | Contents |
| --- | --- |
| `sensor.<name>_skoldag` | Start of the coming school day (today until 17:00, then the next one), with end time and lunch as attributes. |
| `sensor.<name>_kommande_laxor` | Number of upcoming homework and tests; the list is in the `homework` attribute. |
| `calendar.<name>_laxor` | Homework and tests. |
| `calendar.<name>_lediga_dagar` | Holidays and other days off. |

Vklass is checked once an hour. Vklass publishes the lunch menu for this week
and next.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env && chmod 600 .env      # VK_USER / VK_PASS
.venv/bin/python scripts/yclass_cli.py       # print a student's coming weeks
.venv/bin/python -m pytest -q tests
```

Every change bumps `version` in `custom_components/yclass/manifest.json` and
gets a GitHub release; see [CHANGELOG.md](CHANGELOG.md).
