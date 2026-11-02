"""Async client for Vklass, signing in through Göteborgs Stad's student login.

Deliberately free of Home Assistant imports so it can be exercised from the
command line (see scripts/yclass_cli.py).

Flow, as observed 2026-10:
  1. GET auth.vklass.se/saml/initiate?idp=<Göteborg IdP>&org=189
     -> redirects to an IBM ISAM form on auth.goteborg.se
  2. POST username/password to that form
     -> an auto-submitting form carrying SAMLResponse + RelayState
  3. POST those to auth.vklass.se/saml/assertion
     -> redirects to app.vklass.se, session cookies now set on .vklass.se
  4. GET app.vklass.se/Events/FullCalendar?start=YYYY-MM-DD&end=YYYY-MM-DD
     -> JSON list of calendar events; homework is eventType 2 ("Uppgift")
  5. GET app.vklass.se/Assessment/Assignment/Detail/<id>
     -> HTML whose `text-tags` holds "<kind>, <subject>", e.g. "Läxa, Matematik"
  6. GET app.vklass.se/Home/Welcome
     -> the start page; its `school-lunch-items.bind` attribute holds the
        lunch menu as JSON for this week and the next

Since 2026-11-02 app.vklass.se sits behind Baffin Bay's bot protection, which
blocks this client (Blocked). The guardian's calendar subscription
(cal.vklass.se/<id>.ics?custodian=true, parsed by parse_ics) still works: it
has lessons and homework/tests for the coming month, but no lunch, days off,
school events or homework texts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import html
import json
import re
from typing import Any
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import aiohttp
from yarl import URL

APP_URL = "https://app.vklass.se"
AUTH_URL = "https://auth.vklass.se"
GBG_IDP = "https://auth.goteborg.se/idp/sps/skola/saml20"
DEFAULT_ORG = 189

EVENT_TYPE_LECTURE = 1
EVENT_TYPE_ASSIGNMENT = 2
EVENT_TYPE_SCHOOL_EVENT = 4  # "Kalenderhändelse": skolfotografering, utvecklingssamtal, idrottsdag, ...
EVENT_TYPE_VACANT_DAY = 8

_SWEDEN = ZoneInfo("Europe/Stockholm")
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MAX_REDIRECTS = 15
# /Events/FullCalendar silently stops about a month after `start`, so longer
# ranges are fetched in slices of this many days.
_SLICE_DAYS = 28


class VklassError(Exception):
    """Anything that went wrong talking to Vklass."""


class InvalidAuth(VklassError):
    """The IdP rejected the username or password."""


class SessionExpired(VklassError):
    """A data request was bounced to the login page."""


class Blocked(VklassError):
    """Vklass's bot protection (Baffin Bay) blocked or challenged the request."""


@dataclass(slots=True)
class _Reply:
    url: str
    status: int
    content_type: str
    text: str


@dataclass(slots=True)
class Student:
    user_id: int | None
    full_name: str | None
    first_name: str | None


@dataclass(slots=True)
class Homework:
    id: int
    title: str
    due: datetime  # naive, Europe/Stockholm wall-clock time as Vklass reports it
    all_day: bool
    description: str
    url: str
    kind: str | None = None  # "Läxa", "Prov", ... from the detail page
    subject: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "subject": self.subject,
            "due": self.due.isoformat(timespec="minutes"),
            "all_day": self.all_day,
            "description": self.description,
            "url": self.url,
        }


@dataclass(slots=True)
class DayOff:
    title: str
    start: date
    end: date  # inclusive
    description: str


@dataclass(slots=True)
class SchoolEvent:
    title: str
    start: datetime  # naive, Swedish local time
    end: datetime | None
    all_day: bool
    description: str
    url: str


@dataclass(slots=True)
class Lesson:
    title: str
    start: datetime  # naive, Swedish local time
    end: datetime
    cancelled: bool
    code: str = ""  # the short subject code Vklass shows in the schedule, e.g. "IDH"

    @property
    def is_pe(self) -> bool:
        """Idrott och hälsa: the day the student needs gym clothes."""
        return bool(re.search(r"(?i)idrott|gymnastik", self.title)) or self.code.upper().startswith("IDH")


@dataclass(slots=True)
class SchoolDay:
    day: date
    start: datetime  # first lesson starts (naive, Swedish local time)
    end: datetime  # last lesson ends
    lunch: list[str]
    pe: list[Lesson] = field(default_factory=list)  # Idrott och hälsa lessons that day

    def pe_times(self) -> list[dict[str, str]]:
        return [{"start": f"{lesson.start:%H:%M}", "end": f"{lesson.end:%H:%M}"} for lesson in self.pe]

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes"),
            "lunch": self.lunch,
            "pe": self.pe_times(),
        }


@dataclass(slots=True)
class CalendarData:
    homework: list[Homework] = field(default_factory=list)
    days_off: list[DayOff] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    lunch: dict[date, list[str]] = field(default_factory=dict)
    events: list[SchoolEvent] = field(default_factory=list)

    def agenda(self, first: date, last: date) -> list[dict[str, Any]]:
        """Days off and school events touching first..last, in date order.

        Leaves out days off that fall on a weekend only (a Sunday holiday
        changes nothing) and those inside a longer day-off period (Trettondagen
        in the middle of jullovet).
        """
        items: list[tuple[date, dict[str, Any]]] = []
        for off in self.days_off:
            if off.end < first or off.start > last:
                continue
            if all((off.start + timedelta(days=i)).weekday() >= 5 for i in range((off.end - off.start).days + 1)):
                continue
            if any(
                other is not off
                and other.start <= off.start
                and off.end <= other.end
                and (other.end - other.start) > (off.end - off.start)
                for other in self.days_off
            ):
                continue
            items.append(
                (
                    off.start,
                    {
                        "kind": "day_off",
                        "title": off.title,
                        "start": off.start.isoformat(),
                        "end": off.end.isoformat(),
                        "time": None,
                        "description": _drop_title_prefix(off.title, off.description),
                    },
                )
            )
        for ev in self.events:
            day = ev.start.date()
            end_day = ev.end.date() if ev.end and ev.end.date() > day else day
            if end_day < first or day > last:
                continue
            time = None
            if not ev.all_day:
                time = f"{ev.start:%H:%M}" + (f"–{ev.end:%H:%M}" if ev.end else "")
            items.append(
                (
                    day,
                    {
                        "kind": "event",
                        "title": ev.title,
                        "start": day.isoformat(),
                        "end": end_day.isoformat(),
                        "time": time,
                        "description": ev.description,
                    },
                )
            )
        return [item for _, item in sorted(items, key=lambda pair: pair[0])]

    def school_days(self, first: date) -> list[SchoolDay]:
        """Days from `first` on that have at least one lesson not cancelled."""
        by_day: dict[date, list[Lesson]] = {}
        for lesson in self.lessons:
            if not lesson.cancelled and lesson.start.date() >= first:
                by_day.setdefault(lesson.start.date(), []).append(lesson)
        return [
            SchoolDay(
                day=day,
                start=min(lesson.start for lesson in lessons),
                end=max(lesson.end for lesson in lessons),
                lunch=self.lunch.get(day, []),
                pe=[lesson for lesson in lessons if lesson.is_pe],
            )
            for day, lessons in sorted(by_day.items())
        ]


def new_cookie_jar() -> aiohttp.CookieJar:
    """Cookie jar the client needs: values sent back exactly as received.

    auth.goteborg.se sets GBG-SESSION-ID to a value containing '='; aiohttp
    would otherwise send it back in double quotes and the IdP treats that as
    no session at all, looping back to the login form.
    """
    return aiohttp.CookieJar(quote_cookie=False)


def html_to_text(fragment: str | None) -> str:
    """Flatten the Froala HTML Vklass stores in event texts to plain text."""
    if not fragment:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</(div|p|li|h\d)>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _drop_title_prefix(title: str, text: str) -> str:
    """'Studiedag - Skolan är stängd ...' under the title 'Studiedag' -> 'Skolan är stängd ...'."""
    match = re.match(r"\s*([^-–]{1,30}?)\s*[-–]\s*(.+)", text, re.S)
    if match and match.group(1).casefold()[:5] == title.strip().casefold()[:5]:
        return match.group(2).strip()
    return text


def _parse_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M")


def _hidden_inputs(form_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", form_html, re.I):
        if not re.search(r"type\s*=\s*['\"]hidden['\"]", tag, re.I):
            continue
        name = re.search(r"name\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        value = re.search(r"value\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        if name:
            fields[html.unescape(name.group(2))] = html.unescape(value.group(2) if value else "")
    return fields


def _form(page: str, form_id: str | None = None) -> tuple[str, str] | None:
    """Return (action, inner html) of the first form, or of the one with form_id."""
    pattern = (
        rf"<form\b[^>]*\bid=\"{re.escape(form_id)}\"[^>]*>.*?</form>"
        if form_id
        else r"<form\b[^>]*>.*?</form>"
    )
    match = re.search(pattern, page, re.I | re.S)
    if not match:
        return None
    action = re.search(r"\baction\s*=\s*\"([^\"]*)\"", match.group(0), re.I)
    return (html.unescape(action.group(1)) if action else "", match.group(0))


def parse_student(page: str) -> Student:
    def grab(key: str) -> str | None:
        match = re.search(rf'"{key}":("(?:[^"\\]|\\.)*"|\d+|null)', page)
        return json.loads(match.group(1)) if match else None

    user_id = grab("userId")
    return Student(
        user_id=int(user_id) if user_id is not None else None,
        full_name=grab("userFullName"),
        first_name=grab("userFirstName"),
    )


def parse_assignment_tags(page: str) -> tuple[str | None, str | None]:
    """Detail page header, e.g. 'Läxa,  Matematik' -> ('Läxa', 'Matematik')."""
    match = re.search(r'class="text-tags"[^>]*>(.*?)</div>', page, re.S)
    if not match:
        return None, None
    parts = [p.strip() for p in html_to_text(match.group(1)).split(",") if p.strip()]
    kind = parts[0] if parts else None
    subject = ", ".join(parts[1:]) or None
    return kind, subject


def parse_lunch(page: str) -> dict[date, list[str]]:
    """Lunch menu from the start page, {date: [dish, ...]}; days without a menu are left out."""
    match = re.search(r'school-lunch-items\.bind="([^"]*)"', page)
    if not match:
        return {}
    try:
        schools = json.loads(html.unescape(match.group(1)))
    except ValueError:
        return {}
    lunch: dict[date, list[str]] = {}
    for school in schools or []:
        for item in school.get("lunchItems") or []:
            # Dishes come as "Pasta med ... (Fågel), \r": strip the separator.
            dishes = [re.sub(r"[\s,]+$", "", dish).strip() for dish in item.get("menuItems") or []]
            dishes = [dish for dish in dishes if dish]
            if dishes and item.get("date"):
                lunch.setdefault(date.fromisoformat(item["date"]), []).extend(dishes)
    return lunch


def parse_events(events: list[dict[str, Any]]) -> CalendarData:
    data = CalendarData()
    for ev in events:
        etype = ev.get("eventType")
        if etype == EVENT_TYPE_LECTURE and ev.get("start") and ev.get("end") and not ev.get("allDay"):
            data.lessons.append(
                Lesson(
                    title=(ev.get("title") or "").strip(),
                    start=_parse_dt(ev["start"]),
                    end=_parse_dt(ev["end"]),
                    cancelled=bool(ev.get("cancelled")),
                    code=(ev.get("text") or "").strip().splitlines()[0] if (ev.get("text") or "").strip() else "",
                )
            )
        elif etype == EVENT_TYPE_ASSIGNMENT:
            data.homework.append(
                Homework(
                    id=int(ev["lectureId"]),  # Vklass reuses this field as the entity id
                    title=(ev.get("title") or "").strip(),
                    due=_parse_dt(ev["start"]),
                    all_day=bool(ev.get("allDay")),
                    description=html_to_text(ev.get("text")),
                    url=urljoin(APP_URL, ev.get("detailUrl") or ""),
                )
            )
        elif etype == EVENT_TYPE_SCHOOL_EVENT and ev.get("start"):
            data.events.append(
                SchoolEvent(
                    title=(ev.get("title") or "").strip(),
                    start=_parse_dt(ev["start"]),
                    end=_parse_dt(ev["end"]) if ev.get("end") else None,
                    all_day=bool(ev.get("allDay")),
                    description=html_to_text(ev.get("text")),
                    url=urljoin(APP_URL, ev.get("detailUrl") or ""),
                )
            )
        elif etype == EVENT_TYPE_VACANT_DAY:
            start = _parse_dt(ev["start"]).date()
            end = _parse_dt(ev["end"]).date() if ev.get("end") else start
            data.days_off.append(
                DayOff(
                    title=(ev.get("title") or "").strip(),
                    start=start,
                    end=end,
                    description=html_to_text(ev.get("text")),
                )
            )
    _sort(data)
    return data


def _sort(data: CalendarData) -> None:
    data.homework.sort(key=lambda h: (h.due, h.id))
    data.days_off.sort(key=lambda d: d.start)
    data.events.sort(key=lambda e: e.start)
    data.lessons.sort(key=lambda lesson: lesson.start)


def _ics_unescape(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _ics_dt(params: str, value: str) -> tuple[datetime, bool]:
    """(naive Swedish wall-clock time, all day) from a DTSTART/DTEND line."""
    if "VALUE=DATE" in params.upper() and "T" not in value:
        return datetime.strptime(value[:8], "%Y%m%d"), True
    if value.endswith("Z"):
        utc = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        return utc.astimezone(_SWEDEN).replace(tzinfo=None), False
    # Vklass writes TZID=W. Europe Standard Time, i.e. Swedish wall-clock time.
    return datetime.strptime(value[:15], "%Y%m%dT%H%M%S"), False


def _ics_kind(title: str) -> str | None:
    """The feed has no kind; guess it from the title the way the card uses it."""
    if re.search(r"(?i)prov|förhör|diagnos|\btest", title):
        return "Prov"
    if re.search(r"(?i)läxa", title):
        return "Läxa"
    return None


def parse_ics(text: str) -> CalendarData:
    """The guardian's calendar subscription: lessons, homework/tests and any other events.

    Homework is the all-day events linking to ExamInfo.aspx?id=<assignment id>;
    their DESCRIPTION is only "Tisdag 3 november 2026 kl: 08:00.". Lessons have
    "Subject (group)\\ncode\\nteacher" as SUMMARY.
    """
    data = CalendarData()
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", unfolded, re.S):
        props: dict[str, tuple[str, str]] = {}
        for line in block.splitlines():
            name_params, sep, value = line.partition(":")
            if sep:
                name, _, params = name_params.partition(";")
                props[name.upper()] = (params, value.strip())
        if "DTSTART" not in props:
            continue
        start, all_day = _ics_dt(*props["DTSTART"])
        end = _ics_dt(*props["DTEND"])[0] if "DTEND" in props else None
        lines = [line.strip() for line in _ics_unescape(props.get("SUMMARY", ("", ""))[1]).splitlines()]
        title = lines[0] if lines else ""
        description = _ics_unescape(props.get("DESCRIPTION", ("", ""))[1]).strip()
        url = props.get("URL", ("", ""))[1]
        exam = re.search(r"ExamInfo\.aspx\?(?:.*&)?id=(\d+)", url)
        if exam:
            due = start
            clock = re.search(r"kl:\s*(\d{1,2}):(\d{2})", description)
            if clock:
                due = start.replace(hour=int(clock.group(1)), minute=int(clock.group(2)))
            data.homework.append(
                Homework(
                    id=int(exam.group(1)),
                    title=title,
                    due=due,
                    all_day=all_day,
                    description="",
                    url=url,
                    kind=_ics_kind(title),
                )
            )
        elif not all_day and end and re.search(r"\(.+\)$", title):
            data.lessons.append(
                Lesson(title=title, start=start, end=end, cancelled=False, code=lines[1] if len(lines) > 1 else "")
            )
        else:
            data.events.append(
                SchoolEvent(title=title, start=start, end=end, all_day=all_day, description=description, url=url)
            )
    _sort(data)
    return data


async def fetch_ics(session: aiohttp.ClientSession, url: str) -> CalendarData:
    """Fetch and parse a calendar subscription from cal.vklass.se."""
    async with session.get(url, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        text = await resp.text()
    if "BEGIN:VCALENDAR" not in text:
        raise VklassError(f"{url.split('?')[0]} is not a calendar")
    return parse_ics(text)


class VklassClient:
    """One signed-in student.

    `session` should be private to this client (it carries the student's
    cookies) and must use new_cookie_jar().
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        org: int = DEFAULT_ORG,
        idp: str = GBG_IDP,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._initiate_url = f"{AUTH_URL}/saml/initiate?idp={quote(idp, safe='')}&org={org}"
        self._tags: dict[int, tuple[str | None, str | None]] = {}
        self.student: Student | None = None

    async def _request(
        self, method: str, url: str, data: dict[str, str] | None = None, headers: dict[str, str] | None = None
    ) -> _Reply:
        """Follow redirects by hand, passing every URL through byte-for-byte.

        aiohttp's own redirect handling lets yarl re-quote the Location header,
        which turns %2F into / inside the IdP's signed SAMLRequest and makes
        auth.goteborg.se reject it (FBTSML238E).
        """
        for _ in range(_MAX_REDIRECTS):
            async with self._session.request(
                method,
                URL(url, encoded=True),
                data=data,
                headers=headers,
                allow_redirects=False,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                    url = urljoin(url, resp.headers["Location"])
                    if resp.status in (301, 302, 303):
                        method, data = "GET", None
                    continue
                guarded = resp.headers.get("Server", "").startswith("baffin-bay")
                if guarded and resp.status == 403:
                    raise Blocked(f"Vklass's bot protection blocked {url.split('?')[0]}")
                if resp.status in (401, 403):
                    return _Reply(url, resp.status, "", "")
                resp.raise_for_status()
                text = await resp.text()
                if guarded and "/.well-known/baffinbay/" in text:
                    raise Blocked(f"Vklass's bot protection wants a browser check at {url.split('?')[0]}")
                return _Reply(url, resp.status, resp.headers.get("Content-Type", ""), text)
        raise VklassError(f"Too many redirects, last at {url}")

    async def login(self) -> Student:
        reply = await self._request("GET", self._initiate_url)
        page, url = reply.text, reply.url

        if not url.startswith(APP_URL):
            if "SAMLResponse" not in page:  # no live IdP session: fill in the login form
                page, url = await self._submit_credentials(page, url)
            # Follow the SAML auto-submit form(s) back to Vklass.
            for _ in range(5):
                if "SAMLResponse" not in page:
                    break
                form = _form(page)
                if form is None:
                    break
                action, inner = form
                reply = await self._request("POST", urljoin(url, action), data=_hidden_inputs(inner))
                page, url = reply.text, reply.url

        if not url.startswith(APP_URL):
            if _form(page, "loginform") is not None:
                raise InvalidAuth("Göteborgs Stad rejected the username or password")
            raise VklassError(f"Login ended at {url} instead of {APP_URL}")

        student = parse_student(page)
        if student.user_id is None:
            raise VklassError(f"The page at {url} does not say which student is signed in")
        self.student = student
        return student

    async def _submit_credentials(self, page: str, url: str) -> tuple[str, str]:
        form = _form(page, "loginform")
        if form is None:
            raise VklassError(f"Login form not found at {url}")
        action, inner = form
        fields = _hidden_inputs(inner)
        fields.update(username=self._username, password=self._password)
        reply = await self._request("POST", urljoin(url, action), data=fields)
        return reply.text, reply.url

    async def _get(self, path: str, *, json_response: bool) -> Any:
        reply = await self._request(
            "GET",
            urljoin(APP_URL, path),
            headers={"X-Requested-With": "XMLHttpRequest"} if json_response else None,
        )
        if not reply.url.startswith(APP_URL) or reply.status in (401, 403):
            raise SessionExpired(reply.url)
        if json_response:
            if "json" not in reply.content_type:
                raise SessionExpired(f"non-JSON reply from {path}")
            return json.loads(reply.text)
        return reply.text

    async def _get_relogin(self, path: str, *, json_response: bool) -> Any:
        try:
            return await self._get(path, json_response=json_response)
        except SessionExpired:
            await self.login()
            return await self._get(path, json_response=json_response)

    async def fetch(self, start: date, end: date, *, lunch: bool = True) -> CalendarData:
        """Homework, lessons and days off between start and end, homework
        tagged with kind/subject; with `lunch`, also the lunch menu."""
        if self.student is None:
            await self.login()
        events: dict[str, dict[str, Any]] = {}
        slice_start = start
        while slice_start < end:
            slice_end = min(slice_start + timedelta(days=_SLICE_DAYS), end)
            chunk = await self._get_relogin(
                f"/Events/FullCalendar?start={slice_start.isoformat()}&end={slice_end.isoformat()}",
                json_response=True,
            )
            for ev in chunk:  # an event spanning a slice boundary comes back twice
                events[str(ev.get("id"))] = ev
            slice_start = slice_end
        data = parse_events(list(events.values()))
        for hw in data.homework:
            if hw.id not in self._tags:
                page = await self._get_relogin(f"/Assessment/Assignment/Detail/{hw.id}", json_response=False)
                self._tags[hw.id] = parse_assignment_tags(page)
            hw.kind, hw.subject = self._tags[hw.id]
        if lunch:
            data.lunch = parse_lunch(await self._get_relogin("/Home/Welcome", json_response=False))
        return data
