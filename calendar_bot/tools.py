TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_today_events",
            "description": "Get all events for today from Google Calendar",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tomorrow_events",
            "description": "Get all events for tomorrow from Google Calendar",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_week_events",
            "description": "Get all events for the next 7 days from Google Calendar",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events_for_period",
            "description": "Get events for a specific date range. Use ISO format dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "Start date/time in ISO format, e.g. 2026-03-17T00:00:00",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date/time in ISO format, e.g. 2026-03-18T00:00:00",
                    },
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Create a new event in Google Calendar",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Event title/name",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start date/time in ISO format, e.g. 2026-03-20T15:00:00",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date/time in ISO format, e.g. 2026-03-20T16:00:00. If not provided, defaults to 1 hour after start.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description (optional)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location (optional)",
                    },
                    "all_day": {
                        "type": "boolean",
                        "description": "Whether this is an all-day event",
                        "default": False,
                    },
                },
                "required": ["title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": "Update/move an existing event. Use search_events first to find the event ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to update (get from search_events or get_today_events)",
                    },
                    "title": {
                        "type": "string",
                        "description": "New title (optional, only if changing)",
                    },
                    "start": {
                        "type": "string",
                        "description": "New start time in ISO format (optional, for rescheduling)",
                    },
                    "end": {
                        "type": "string",
                        "description": "New end time in ISO format (optional, for rescheduling)",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (optional)",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Delete an event from the calendar. Use search_events first to find the event ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to delete",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "Search for events by keyword in the next N days",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (event title, description, etc.)",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of days to search ahead (default 30)",
                        "default": 30,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_free_slots",
            "description": "Get free and busy time slots for a specific date (8:00-22:00)",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in ISO format, e.g. 2026-03-20. If not provided, uses today.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overdue_events",
            "description": "Get events from the past 7 days that are already over (potentially overdue tasks)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
