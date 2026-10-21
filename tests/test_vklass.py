"""Offline tests for the Vklass parsing (no network, no Home Assistant)."""

from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys

# Loaded by path: the integration directory on sys.path would shadow stdlib calendar.
_spec = importlib.util.spec_from_file_location(
    "vklass", Path(__file__).resolve().parent.parent / "custom_components" / "yclass" / "vklass.py"
)
vklass = importlib.util.module_from_spec(_spec)
sys.modules["vklass"] = vklass
_spec.loader.exec_module(vklass)

_form = vklass._form
_hidden_inputs = vklass._hidden_inputs
html_to_text = vklass.html_to_text
parse_assignment_tags = vklass.parse_assignment_tags
parse_events = vklass.parse_events
parse_student = vklass.parse_student

EVENTS = [
    {
        "id": "Lecture1", "title": "Svenska", "start": "2026-10-19 08:10", "end": "2026-10-19 09:20",
        "allDay": False, "text": "SV", "detailUrl": "/Calendar/Lecture/1", "eventType": 1, "lectureId": 1,
    },
    {
        "id": "Assignment4889972", "title": "Läxförhör tabell 4 och 9 ", "start": "2026-10-23 08:10", "end": None,
        "allDay": True, "detailUrl": "/Assessment/Assignment/Detail/4889972", "eventType": 2, "lectureId": 4889972,
        "text": '<div style="font-size: 14px;">Läxförhör tabell 4 och 9<div style="font-size: 14px;"><br></div></div>',
    },
    {
        "id": "Assignment4881597", "title": "Läxa, begrepp meteorologi", "start": "2026-10-07 10:25", "end": None,
        "allDay": True, "detailUrl": "/Assessment/Assignment/Detail/4881597", "eventType": 2, "lectureId": 4881597,
        "text": "<div>Klassen har&nbsp;begrepp</div><div>i l&auml;xa.</div>",
    },
    {
        "id": "VacantDay56587", "title": "Läslov", "start": "2026-10-27 00:00", "end": "2026-10-30 23:59",
        "allDay": True, "text": "Skolan är stängd", "detailUrl": "/Calendar/VacantDayEvent/56587", "eventType": 8,
        "lectureId": 56587,
    },
]


def test_parse_events() -> None:
    data = parse_events(EVENTS)
    assert [hw.id for hw in data.homework] == [4881597, 4889972]  # sorted by due date
    first, second = data.homework
    assert first.description == "Klassen har begrepp\ni läxa."
    assert second.title == "Läxförhör tabell 4 och 9"
    assert second.due == datetime(2026, 10, 23, 8, 10)
    assert second.all_day
    assert second.url == "https://app.vklass.se/Assessment/Assignment/Detail/4889972"
    assert len(data.days_off) == 1
    assert (data.days_off[0].start, data.days_off[0].end) == (date(2026, 10, 27), date(2026, 10, 30))


def test_assignment_tags() -> None:
    page = '<header><div class="text-tags">Läxa,  Matematik</div><h1>x</h1>'
    assert parse_assignment_tags(page) == ("Läxa", "Matematik")
    assert parse_assignment_tags('<div class="text-tags">Prov</div>') == ("Prov", None)
    assert parse_assignment_tags("<html></html>") == (None, None)


def test_student() -> None:
    page = '..."userId":123456,"custodianUserId":null,"userFullName":"Elev Exempel","userFirstName":"Elev",...'
    student = parse_student(page)
    assert (student.user_id, student.full_name, student.first_name) == (123456, "Elev Exempel", "Elev")


def test_login_form_inputs() -> None:
    page = """
    <form id="rememberform" action="/other" method="POST"><input type="hidden" name="x" value="1"></form>
    <form class="c-form" method="POST" name="loginform" id="loginform" action="/mga/sps?StateId=a&amp;b=1">
      <input class="c-input-text" type="text" id="username" name="username" required>
      <input type="hidden" name="operation" value="verify">
      <input type='hidden' name='templateTarget' value='https://auth.goteborg.se/idp/sps/auth?FedId=u'>
    </form>"""
    action, inner = _form(page, "loginform")
    assert action == "/mga/sps?StateId=a&b=1"
    assert _hidden_inputs(inner) == {
        "operation": "verify",
        "templateTarget": "https://auth.goteborg.se/idp/sps/auth?FedId=u",
    }


def test_html_to_text_empty() -> None:
    assert html_to_text(None) == ""
    assert html_to_text("<div><br></div>") == ""


def test_lessons_and_school_days() -> None:
    lesson = lambda start, end, cancelled=False: {  # noqa: E731
        "id": f"Lecture{start}", "title": "Svenska", "start": start, "end": end, "allDay": False,
        "eventType": 1, "lectureId": 1, "cancelled": cancelled,
    }
    data = parse_events(
        [
            lesson("2026-10-20 10:00", "2026-10-20 13:25"),
            lesson("2026-10-20 08:10", "2026-10-20 09:00"),
            lesson("2026-10-20 14:00", "2026-10-20 15:30", cancelled=True),  # ignored
            lesson("2026-10-21 08:40", "2026-10-21 14:30"),
            lesson("2026-10-19 08:10", "2026-10-19 13:50"),  # before `first`
        ]
    )
    data.lessons.append(
        vklass.Lesson("Idrott och hälsa", datetime(2026, 10, 21, 12, 5), datetime(2026, 10, 21, 13, 0), False, "IDH")
    )
    data.lunch = {date(2026, 10, 20): ["Fiskpudding (Fisk)"]}
    days = data.school_days(date(2026, 10, 20))
    assert [d.day for d in days] == [date(2026, 10, 20), date(2026, 10, 21)]
    assert (days[0].start, days[0].end) == (datetime(2026, 10, 20, 8, 10), datetime(2026, 10, 20, 13, 25))
    assert days[0].lunch == ["Fiskpudding (Fisk)"]
    assert days[1].lunch == []
    assert days[0].pe == []
    assert days[1].pe_times() == [{"start": "12:05", "end": "13:00"}]
    assert vklass.Lesson("Musik", days[0].start, days[0].end, False, "MU").is_pe is False


def test_parse_lunch() -> None:
    page = (
        '<vk-lunch school-lunch-items.bind="[{&quot;schoolId&quot;:1,&quot;lunchItems&quot;:['
        "{&quot;id&quot;:5,&quot;menuItems&quot;:[&quot;Pasta med ost (Fågel), \\r&quot;,"
        "&quot;Linsgryta (Vegetariskt)&quot;],&quot;date&quot;:&quot;2026-10-20&quot;},"
        '{&quot;id&quot;:-1,&quot;menuItems&quot;:[],&quot;date&quot;:&quot;2026-10-26&quot;}]}]"></vk-lunch>'
    )
    assert vklass.parse_lunch(page) == {date(2026, 10, 20): ["Pasta med ost (Fågel)", "Linsgryta (Vegetariskt)"]}
    assert vklass.parse_lunch("<html></html>") == {}


def test_agenda() -> None:
    def off(start, end, title, text=""):
        return {"id": f"VacantDay{start}{title}", "title": title, "start": f"{start} 00:00", "end": f"{end} 23:59",
                "allDay": True, "text": text, "eventType": 8, "lectureId": 1}

    data = parse_events(
        [
            off("2026-10-26", "2026-10-26", "Läslov", "Skolan och fritidshemmet är stängda"),
            off("2026-10-27", "2026-10-30", "Läslov"),
            off("2026-10-31", "2026-10-31", "Alla helgons dag"),  # Saturday: left out
            off("2027-01-04", "2027-01-06", "Lovdag", "Lovdagar - Skolan är stängd"),
            off("2027-01-06", "2027-01-06", "Trettondagen"),  # inside the longer Lovdag: left out
            {"id": "SchoolEvent1", "title": "Skolfotografering", "start": "2026-10-28 00:00", "end": None,
             "allDay": True, "text": "", "eventType": 4, "lectureId": 1, "detailUrl": "/Calendar/SchoolEvent/1"},
            {"id": "SchoolEvent2", "title": "Utvecklingssamtal", "start": "2026-11-02 14:00",
             "end": "2026-11-02 14:30", "allDay": False, "eventType": 4, "lectureId": 2},
        ]
    )
    items = data.agenda(date(2026, 10, 21), date(2027, 1, 31))
    assert [(i["start"], i["title"]) for i in items] == [
        ("2026-10-26", "Läslov"),
        ("2026-10-27", "Läslov"),
        ("2026-10-28", "Skolfotografering"),
        ("2026-11-02", "Utvecklingssamtal"),
        ("2027-01-04", "Lovdag"),
    ]
    assert items[0]["description"] == "Skolan och fritidshemmet är stängda"
    assert items[1]["end"] == "2026-10-30"
    assert items[4]["description"] == "Skolan är stängd"
    assert items[3]["time"] == "14:00–14:30" and items[3]["kind"] == "event"
    assert data.agenda(date(2026, 11, 3), date(2026, 12, 1)) == []
