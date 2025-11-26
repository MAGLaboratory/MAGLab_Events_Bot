"""Google Calendar ICS ingestion and normalization services."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

import pendulum
import requests
from dateutil.rrule import rrulestr
from icalendar import Calendar
from pendulum.tz.timezone import FixedTimezone, Timezone

from maglab_events_bot.models.events import (
    CalendarEvent,
    CancelledCalendarEvent,
)
from maglab_events_bot.utils.formatting import truncate_description
from maglab_events_bot.utils.http import build_session

logger = logging.getLogger(__name__)


@runtime_checkable
class _SupportsTotalSeconds(Protocol):
    def total_seconds(self) -> float:
        """Return total seconds for duration-like values."""
        raise NotImplementedError


class CalendarFetcher:
    """Fetch and normalize ICS feed events from Google Calendars."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or build_session()

    async def fetch_events(
        self,
        urls: Iterable[str],
        *,
        sync_horizon_days: int,
        timezone_name: str,
    ) -> Tuple[List[CalendarEvent], List[CancelledCalendarEvent]]:
        events: List[CalendarEvent] = []
        cancellations: List[CancelledCalendarEvent] = []

        now_utc = pendulum.now("UTC")
        future_window = now_utc.add(days=sync_horizon_days)
        tz = pendulum.timezone(timezone_name)

        summary = {"created": 0, "canceled": 0}

        for url in urls:
            try:
                feed_events, feed_cancellations = await self._fetch_single_calendar(
                    url,
                    now_utc,
                    future_window,
                    tz,
                )
                events.extend(feed_events)
                cancellations.extend(feed_cancellations)
                summary["created"] += len(feed_events)
                summary["canceled"] += len(feed_cancellations)
            except requests.RequestException as exc:
                logger.warning("calendar.fetch_error", extra={"url": url, "error": str(exc)})
            except Exception:  # pylint: disable=broad-except
                logger.exception("calendar.ingest_failure", extra={"url": url})
        logger.info(
            "calendar.sync_summary",
            extra={
                "total_events": len(events),
                "total_cancellations": len(cancellations),
                "per_feed": summary,
            },
        )
        return events, cancellations

    async def _fetch_single_calendar(
        self,
        url: str,
        window_start: pendulum.DateTime,
        window_end: pendulum.DateTime,
        tz: Timezone,
    ) -> Tuple[List[CalendarEvent], List[CancelledCalendarEvent]]:
        events: List[CalendarEvent] = []
        cancellations: List[CancelledCalendarEvent] = []

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, self.session.get, url)
        response.raise_for_status()
        calendar = Calendar.from_ical(response.content)

        exceptions: Dict[str, List] = {}
        recurrence_cancellations: Dict[str, Dict[pendulum.DateTime, Dict[str, Any]]] = {}
        full_cancellations: set = set()

        for component in calendar.walk():
            if component.name != "VEVENT":
                continue
            status = str(component.get("status", "")).upper()
            uid = str(component.get("uid"))
            recurrence_id = component.get("recurrence-id")

            if recurrence_id:
                occurrence_id = self._normalize_date(recurrence_id.dt).in_timezone("UTC")
                if status == "CANCELLED":
                    cancellations_for_uid = recurrence_cancellations.setdefault(uid, {})
                    cancellations_for_uid[occurrence_id] = {"component": component}
                else:
                    exceptions.setdefault(uid, []).append(component)
                continue

            if status == "CANCELLED":
                full_cancellations.add(uid)

            exdate_fields = component.get("exdate")
            if exdate_fields:
                if not isinstance(exdate_fields, list):
                    exdate_fields = [exdate_fields]
                for exdate_field in exdate_fields:
                    for exdate_value in getattr(exdate_field, "dts", []) or []:
                        occurrence_id = self._normalize_date(exdate_value.dt, tz).in_timezone("UTC")
                        cancellations_for_uid = recurrence_cancellations.setdefault(uid, {})
                        cancellations_for_uid.setdefault(occurrence_id, {})

        for component in calendar.walk():
            if component.name != "VEVENT":
                continue

            uid = str(component.get("uid"))
            status = str(component.get("status", "")).upper()

            if component.get("recurrence-id"):
                continue

            if uid in full_cancellations:
                continue

            if status == "CANCELLED" and component.get("recurrence-id"):
                continue

            start, end = self._extract_start_end(component, tz)
            event_duration = end - start

            summary = component.get("summary", "Untitled Event").strip()
            description = truncate_description(component.get("description", "").strip())
            location = component.get("location", "MAG Laboratory").strip() or "MAG Laboratory"

            rrule_field = component.get("rrule")
            if rrule_field:
                rrule_str = self._adjust_rrule_for_utc(rrule_field.to_ical().decode("utf-8"), start)
                try:
                    rule = rrulestr(rrule_str, dtstart=start)
                    occurrences = rule.between(
                        window_start.in_timezone(tz), window_end.in_timezone(tz)
                    )
                except ValueError as exc:
                    logger.error("RRULE error for %s: %s", summary, exc)
                    continue

                for occurrence_start in occurrences:
                    occ_start = pendulum.instance(occurrence_start, tz=tz).replace(
                        second=0, microsecond=0
                    )
                    occ_end = occ_start + event_duration
                    occurrence_id = occ_start.in_timezone("UTC")

                    cancel_entry = recurrence_cancellations.get(uid, {}).get(occurrence_id)
                    cancel_component = None
                    if cancel_entry is not None:
                        cancel_component = cancel_entry.get("component")

                    exception_component = self._match_exception(exceptions.get(uid, []), occ_start)

                    # If we have an explicit CANCELLED component for this occurrence, honor it
                    if cancel_component is not None:
                        cancel_summary_value = self._safe_component_get(cancel_component, "summary")
                        cancel_summary_str = summary
                        if cancel_summary_value is not None:
                            candidate = str(cancel_summary_value).strip()
                            if candidate:
                                cancel_summary_str = candidate

                        cancel_description_value = self._safe_component_get(
                            cancel_component, "description"
                        )
                        cancel_description_str = description
                        if cancel_description_value is not None:
                            candidate = str(cancel_description_value).strip()
                            if candidate:
                                cancel_description_str = truncate_description(candidate)

                        cancel_location_value = self._safe_component_get(
                            cancel_component, "location"
                        )
                        cancel_location_str = location
                        if cancel_location_value is not None:
                            candidate = str(cancel_location_value).strip()
                            if candidate:
                                cancel_location_str = candidate

                        cancel_start_field = self._safe_component_get(cancel_component, "dtstart")
                        cancel_end_field = self._safe_component_get(cancel_component, "dtend")

                        cancel_start = occ_start.in_timezone("UTC")
                        if cancel_start_field and hasattr(cancel_start_field, "dt"):
                            cancel_start = self._normalize_date(
                                cancel_start_field.dt, tz
                            ).in_timezone("UTC")

                        if cancel_end_field and hasattr(cancel_end_field, "dt"):
                            cancel_end = self._normalize_date(cancel_end_field.dt, tz).in_timezone(
                                "UTC"
                            )
                        else:
                            cancel_end = (occ_start + event_duration).in_timezone("UTC")

                        cancellations.append(
                            CancelledCalendarEvent(
                                uid=uid,
                                name=cancel_summary_str,
                                description=cancel_description_str,
                                start_time=cancel_start,
                                end_time=cancel_end,
                                location=cancel_location_str or location,
                            )
                        )
                        continue

                    if exception_component is not None:
                        ex_summary = exception_component.get("summary", summary).strip()
                        ex_description = truncate_description(
                            exception_component.get("description", description).strip()
                        )
                        ex_location = (
                            exception_component.get("location", location).strip() or location
                        )

                        ex_start_field = exception_component.get("dtstart")
                        if ex_start_field:
                            ex_start_local = self._normalize_date(ex_start_field.dt, tz)
                        else:
                            ex_start_local = occ_start

                        ex_end_component = exception_component.get("dtend")
                        if ex_end_component:
                            ex_end_local = self._normalize_date(ex_end_component.dt, tz)
                        else:
                            ex_end_local = ex_start_local + event_duration

                        if not self._within_window(
                            ex_start_local, ex_end_local, window_start.in_timezone(tz), window_end
                        ):
                            continue

                        events.append(
                            CalendarEvent(
                                uid=uid,
                                name=ex_summary,
                                description=ex_description,
                                start_time=ex_start_local.in_timezone("UTC"),
                                end_time=ex_end_local.in_timezone("UTC"),
                                location=ex_location,
                            )
                        )
                        continue

                    if cancel_entry is not None:
                        # No explicit CANCELLED component, but EXDATE removed this occurrence.
                        cancellations.append(
                            CancelledCalendarEvent(
                                uid=uid,
                                name=summary,
                                description=description,
                                start_time=occ_start.in_timezone("UTC"),
                                end_time=occ_end.in_timezone("UTC"),
                                location=location,
                            )
                        )
                        continue

                    events.append(
                        CalendarEvent(
                            uid=uid,
                            name=summary,
                            description=description,
                            start_time=occ_start.in_timezone("UTC"),
                            end_time=occ_end.in_timezone("UTC"),
                            location=location,
                        )
                    )

                # Handle RDATE occurrences that are outside the RRULE expansion
                rdate_fields = component.get("rdate")
                if rdate_fields:
                    if not isinstance(rdate_fields, list):
                        rdate_fields = [rdate_fields]
                    rdate_values = []
                    for rdate_field in rdate_fields:
                        rdate_values.extend(getattr(rdate_field, "dts", []) or [])

                    for rdate_value in rdate_values:
                        occ_start = self._normalize_date(rdate_value.dt, tz).replace(
                            second=0, microsecond=0
                        )
                        occ_end = occ_start + event_duration
                        occurrence_id = occ_start.in_timezone("UTC")

                        if not self._within_window(
                            occ_start, occ_end, window_start.in_timezone(tz), window_end
                        ):
                            continue

                        cancel_entry = recurrence_cancellations.get(uid, {}).get(occurrence_id)
                        exception_component = self._match_exception(exceptions.get(uid, []), occ_start)

                        if cancel_entry is not None:
                            cancel_summary_value = cancel_entry.get("component", {}).get("summary")
                            cancel_summary_str = summary
                            if cancel_summary_value is not None:
                                candidate = str(cancel_summary_value).strip()
                                if candidate:
                                    cancel_summary_str = candidate

                            cancel_description_value = cancel_entry.get("component", {}).get("description")
                            cancel_description_str = description
                            if cancel_description_value is not None:
                                candidate = str(cancel_description_value).strip()
                                if candidate:
                                    cancel_description_str = truncate_description(candidate)

                            cancel_location_value = cancel_entry.get("component", {}).get("location")
                            cancel_location_str = location
                            if cancel_location_value is not None:
                                candidate = str(cancel_location_value).strip()
                                if candidate:
                                    cancel_location_str = candidate

                            cancellations.append(
                                CancelledCalendarEvent(
                                    uid=uid,
                                    name=cancel_summary_str,
                                    description=cancel_description_str,
                                    start_time=occ_start.in_timezone("UTC"),
                                    end_time=occ_end.in_timezone("UTC"),
                                    location=cancel_location_str or location,
                                )
                            )
                            continue

                        if exception_component is not None:
                            ex_summary = exception_component.get("summary", summary).strip()
                            ex_description = truncate_description(
                                exception_component.get("description", description).strip()
                            )
                            ex_location = (
                                exception_component.get("location", location).strip() or location
                            )

                            ex_start_field = exception_component.get("dtstart")
                            if ex_start_field:
                                ex_start_local = self._normalize_date(ex_start_field.dt, tz)
                            else:
                                ex_start_local = occ_start

                            ex_end_component = exception_component.get("dtend")
                            if ex_end_component:
                                ex_end_local = self._normalize_date(ex_end_component.dt, tz)
                            else:
                                ex_end_local = ex_start_local + event_duration

                            if not self._within_window(
                                ex_start_local, ex_end_local, window_start.in_timezone(tz), window_end
                            ):
                                continue

                            events.append(
                                CalendarEvent(
                                    uid=uid,
                                    name=ex_summary,
                                    description=ex_description,
                                    start_time=ex_start_local.in_timezone("UTC"),
                                    end_time=ex_end_local.in_timezone("UTC"),
                                    location=ex_location,
                                )
                            )
                            continue

                        events.append(
                            CalendarEvent(
                                uid=uid,
                                name=summary,
                                description=description,
                                start_time=occ_start.in_timezone("UTC"),
                                end_time=occ_end.in_timezone("UTC"),
                                location=location,
                            )
                        )
            else:
                start_utc = start.in_timezone("UTC")
                end_utc = end.in_timezone("UTC")
                if end_utc < window_start or start_utc > window_end:
                    continue
                if status == "CANCELLED":
                    cancellations.append(
                        CancelledCalendarEvent(
                            uid=uid,
                            name=summary,
                            description=description,
                            start_time=start_utc,
                            end_time=end_utc,
                            location=location,
                        )
                    )
                else:
                    events.append(
                        CalendarEvent(
                            uid=uid,
                            name=summary,
                            description=description,
                            start_time=start_utc,
                            end_time=end_utc,
                            location=location,
                        )
                    )

        return events, cancellations

    @staticmethod
    def _normalize_date(
        value,
        default_tz: Timezone | FixedTimezone | None = None,
    ) -> pendulum.DateTime:
        """Normalize ical date/date-time values into timezone-aware pendulum DateTime."""
        tz = default_tz or pendulum.timezone("UTC")

        if isinstance(value, pendulum.DateTime):
            return value.in_timezone(tz)

        if isinstance(value, pendulum.Date):
            return pendulum.datetime(value.year, value.month, value.day, tz=tz)

        if hasattr(value, "tzinfo") and value.tzinfo:
            return pendulum.instance(value).in_timezone(tz)

        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            return pendulum.datetime(value.year, value.month, value.day, tz=tz)

        return pendulum.instance(value, tz=tz)

    @staticmethod
    def _extract_start_end(component, tz: Timezone) -> Tuple[pendulum.DateTime, pendulum.DateTime]:
        dtstart = component.get("dtstart")
        if not dtstart:
            raise ValueError("VEVENT is missing DTSTART")
        start = CalendarFetcher._normalize_date(dtstart.dt, tz)

        dtend_field = component.get("dtend")
        duration_field = component.get("duration")
        end: Optional[pendulum.DateTime] = None

        if dtend_field:
            end = CalendarFetcher._normalize_date(dtend_field.dt, tz)
        elif duration_field:
            duration_value = duration_field.dt
            seconds: Optional[float] = None
            if isinstance(duration_value, _SupportsTotalSeconds):
                seconds = float(duration_value.total_seconds())
            elif isinstance(duration_value, (int, float)):
                seconds = float(duration_value)
            if seconds is not None:
                end = start + pendulum.duration(seconds=seconds)

        if end is None:
            end = start.add(hours=1)

        if end <= start:
            logger.warning("Calendar event end not after start; adjusting duration")
            end = start.add(minutes=1)

        return start, end

    @staticmethod
    def _adjust_rrule_for_utc(rrule_str: str, start: pendulum.DateTime) -> str:
        if "UNTIL" not in rrule_str:
            return rrule_str

        parts = rrule_str.split(";")
        for idx, part in enumerate(parts):
            if part.startswith("UNTIL="):
                raw_value = part.split("=", 1)[1]
                try:
                    parsed_until = pendulum.parse(raw_value)
                except pendulum.parsing.exceptions.ParserError:
                    logger.error("Unable to parse UNTIL value %s", raw_value)
                    continue
                if isinstance(parsed_until, pendulum.DateTime):
                    source = parsed_until
                    if parsed_until.timezone is None:
                        source = parsed_until.set(tz=start.timezone)
                elif isinstance(parsed_until, pendulum.Date):
                    source = pendulum.datetime(
                        parsed_until.year,
                        parsed_until.month,
                        parsed_until.day,
                        tz=start.timezone,
                    )
                else:
                    logger.warning("Unsupported UNTIL value type: %s", type(parsed_until))
                    continue
                parts[idx] = "UNTIL=" + source.in_timezone("UTC").strftime("%Y%m%dT%H%M%SZ")
        return ";".join(parts)

    @staticmethod
    def _within_window(
        start: pendulum.DateTime,
        end: pendulum.DateTime,
        window_start: pendulum.DateTime,
        window_end: pendulum.DateTime,
    ) -> bool:
        """Return True if any part of the event falls within the sync window."""
        return not (end < window_start or start > window_end)

    @staticmethod
    def _safe_component_get(component: Any | None, key: str) -> Any | None:
        if component is None or not hasattr(component, "get"):
            return None
        return component.get(key)

    @staticmethod
    def _match_exception(exceptions: List, occurrence_start: pendulum.DateTime):
        for comp in exceptions:
            recurrence_id = comp.get("recurrence-id")
            if not recurrence_id:
                continue
            occurrence_tz = occurrence_start.timezone or pendulum.timezone("UTC")
            normalized = CalendarFetcher._normalize_date(
                recurrence_id.dt, occurrence_tz
            ).in_timezone("UTC")
            if normalized == occurrence_start.in_timezone("UTC"):
                return comp
        return None
