# Changelog

## 0.5.0 (2026-10-19)

- Sensor *Skoldag*: when the coming school day starts and ends, and its lunch.
  It switches to the next school day at 17:00.
- Lunch menu from the Vklass start page.
- Dashboard card `custom:yclass-card`, served and loaded by the integration:
  first name, start/end, lunch, homework and tests in order. Pick the student
  in the visual editor.
- The calendar is fetched in 28-day slices; Vklass cuts longer ranges short.
- HACS support (`hacs.json`).

## 0.1.0

- First version: homework and days-off calendars, upcoming homework sensor.
