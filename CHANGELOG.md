# Changelog

## 1.4.0 (2026-11-02)

- Teachers often write the real homework on the lesson in the schedule
  (glosor, what to read, "ta med dator"). yclass now keeps that text: the
  card has a new section *Från lektionerna* with the coming two weeks'
  lessons that have a note (tap to read, links open), and the schoolday
  sensor has them in `lesson_notes`.
- Homework from the calendar subscription that is due at the start of a
  lesson with a note gets that note as its text, labelled with the lesson
  (e.g. "Tyska 11:35: Glosor: …").
- Links in homework texts are clickable in the card.

## 1.3.0 (2026-11-02)

- Vklass has put bot protection (Baffin Bay) in front of app.vklass.se, which
  blocks the sign-in. yclass now recognises that, waits 6 hours (doubling up to
  24) before trying again, and logs it once instead of every hour.
- New option *Calendar link (iCal)* (*Configure* on the integration): the
  guardian's calendar subscription from cal.vklass.se. While Vklass blocks the
  sign-in, lessons (school day start/end, Idrott) and homework/tests come from
  there. Lunch, days off, school events and homework texts are not in the
  subscription; they are kept from the last successful sign-in.
- If neither answers, the entities keep the last data instead of going
  unavailable, so the card no longer goes blank.
- Card: a status line when the data is from the subscription or old.
- The sign-in no longer counts as successful when Vklass answers with a page
  that does not name the student.

## 1.2.0 (2026-10-21)

- Card: new section *Lov och händelser* with days off (studiedag, lov,
  holidays on weekdays) and school events (e.g. skolfotografering,
  utvecklingssamtal, idrottsdag) for the coming 30 days.
- New calendar *Händelser* with the school events; the schoolday sensor has
  the 30-day list in the `agenda` attribute.
- The Idrott row no longer says "ta med idrottskläder".

## 1.1.0 (2026-10-21)

- Card: a row with an *Idrott* label and the lesson time when the school day
  has Idrott och hälsa. The schoolday sensor has it in the `pe` attribute.

## 1.0.0 (2026-10-20)

- First stable release. No functional changes since 0.5.1.
- Shorter README focused on installing and using the integration.

## 0.5.1 (2026-10-19)

- The card is suggested under *Add card → By entity* when a yclass entity is
  picked (HA 2026.10+). Before, it only showed under *By card*.

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
