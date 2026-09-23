from datetime import date

import pytest

from maglab_events_bot.services.business_hours import BusinessHoursPlan
from maglab_events_bot.services.business_profile import merge_special_periods, publish_business_hours


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class FakeSession:
    def __init__(self):
        self.patches = []

    def post(self, _url, **_kwargs):
        return FakeResponse({"access_token": "test-token"})

    def get(self, _url, **_kwargs):
        return FakeResponse(
            {
                "specialHours": {
                    "specialHourPeriods": [
                        {"startDate": {"year": 2026, "month": 9, "day": 20}, "closed": True},
                        {"startDate": {"year": 2026, "month": 9, "day": 22}, "closed": True},
                    ]
                }
            }
        )

    def patch(self, _url, **kwargs):
        self.patches.append(kwargs)
        return FakeResponse({})


def test_publish_validates_and_preserves_special_hours_outside_window():
    session = FakeSession()
    plan = BusinessHoursPlan(
        regular_periods=[
            {"openDay": "MONDAY", "openTime": {"hours": 10, "minutes": 0}, "closeDay": "MONDAY", "closeTime": {"hours": 12, "minutes": 0}}
        ],
        special_periods=[
            {"startDate": {"year": 2026, "month": 9, "day": 22}, "openTime": {"hours": 13, "minutes": 0}, "closeTime": {"hours": 15, "minutes": 0}}
        ],
    )
    publish_business_hours(
        plan,
        first_date=date(2026, 9, 21),
        last_date=date(2026, 10, 4),
        location="locations/12345",
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        session=session,
    )

    assert len(session.patches) == 2
    assert session.patches[0]["params"]["validateOnly"] == "true"
    assert session.patches[1]["params"] == {"updateMask": "regularHours,specialHours"}
    special = session.patches[1]["json"]["specialHours"]["specialHourPeriods"]
    assert [period["startDate"]["day"] for period in special] == [20, 22]
    assert special[1]["openTime"] == {"hours": 13, "minutes": 0}


def test_publish_rejects_absent_regular_hours():
    with pytest.raises(ValueError, match="regular hours"):
        publish_business_hours(
            BusinessHoursPlan([], []),
            first_date=date(2026, 9, 21),
            last_date=date(2026, 10, 4),
            location="locations/12345",
            client_id="id",
            client_secret="secret",
            refresh_token="refresh",
            session=FakeSession(),
        )


def test_merge_special_periods_rejects_cross_boundary_entry():
    with pytest.raises(ValueError, match="crosses"):
        merge_special_periods(
            [
                {
                    "startDate": {"year": 2026, "month": 9, "day": 20},
                    "endDate": {"year": 2026, "month": 9, "day": 21},
                    "openTime": "21:00",
                    "closeTime": "02:00",
                }
            ],
            [],
            first_date=date(2026, 9, 21),
            last_date=date(2026, 10, 4),
        )
