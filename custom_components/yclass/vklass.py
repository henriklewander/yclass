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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import html
import json
import re
from typing import Any
from urllib.parse import quote, urljoin

import aiohttp
from yarl import URL

APP_URL = "https://app.vklass.se"
AUTH_URL = "https://auth.vklass.se"
GBG_IDP = "https://auth.goteborg.se/idp/sps/skola/saml20"
DEFAULT_ORG = 189

EVENT_TYPE_LECTURE = 1
EVENT_TYPE_ASSIGNMENT = 2
EVENT_TYPE_VACANT_DAY = 8

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
    data.homework.sort(key=lambda h: (h.due, h.id))
    data.days_off.sort(key=lambda d: d.start)
    data.lessons.sort(key=lambda lesson: lesson.start)
    return data


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
                if resp.status in (401, 403):
                    return _Reply(url, resp.status, "", "")
                resp.raise_for_status()
                return _Reply(url, resp.status, resp.headers.get("Content-Type", ""), await resp.text())
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

        self.student = parse_student(page)
        return self.student

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
