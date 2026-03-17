import aiohttp
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CalendarAPI:
    def __init__(self, script_url: str, script_token: str):
        self.script_url = script_url
        self.script_token = script_token
        self.session: aiohttp.ClientSession | None = None

    async def init(self) -> None:
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def _get(self, action: str, **params: str) -> dict[str, Any]:
        assert self.session is not None
        params["action"] = action
        params["token"] = self.script_token
        try:
            async with self.session.get(
                self.script_url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                text = await resp.text()
                return json.loads(text)
        except Exception as e:
            logger.error("Calendar API GET error: %s", e)
            return {"error": str(e)}

    async def _post(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        assert self.session is not None
        params = {"action": action, "token": self.script_token}
        try:
            async with self.session.post(
                self.script_url,
                params=params,
                json=data,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                return json.loads(text)
        except Exception as e:
            logger.error("Calendar API POST error: %s", e)
            return {"error": str(e)}

    async def get_today_events(self) -> dict[str, Any]:
        return await self._get("getTodayEvents")

    async def get_tomorrow_events(self) -> dict[str, Any]:
        return await self._get("getTomorrowEvents")

    async def get_week_events(self) -> dict[str, Any]:
        return await self._get("getWeekEvents")

    async def get_events(self, start: str, end: str) -> dict[str, Any]:
        return await self._get("getEvents", start=start, end=end)

    async def get_upcoming(self, minutes: int = 30) -> dict[str, Any]:
        return await self._get("getUpcoming", minutes=str(minutes))

    async def get_overdue(self) -> dict[str, Any]:
        return await self._get("getOverdue")

    async def create_event(
        self,
        title: str,
        start: str,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        all_day: bool = False,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"title": title, "start": start, "allDay": all_day}
        if end:
            data["end"] = end
        if description:
            data["description"] = description
        if location:
            data["location"] = location
        return await self._post("createEvent", data)

    async def update_event(
        self,
        event_id: str,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"eventId": event_id}
        if title:
            data["title"] = title
        if start:
            data["start"] = start
        if end:
            data["end"] = end
        if description:
            data["description"] = description
        if location:
            data["location"] = location
        return await self._post("updateEvent", data)

    async def delete_event(self, event_id: str) -> dict[str, Any]:
        return await self._post("deleteEvent", {"eventId": event_id})

    async def search_events(self, query: str, days: int = 30) -> dict[str, Any]:
        return await self._get("searchEvents", query=query, days=str(days))

    async def get_free_busy(self, date: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if date:
            params["date"] = date
        return await self._get("getFreeBusy", **params)
