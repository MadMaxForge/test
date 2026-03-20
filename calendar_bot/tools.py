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
            "description": "Delete an event from the calendar. ALWAYS provide title and start along with event_id for reliable deletion (especially for recurring events).",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to delete",
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title (for fallback search if ID lookup fails)",
                    },
                    "start": {
                        "type": "string",
                        "description": "Event start time in ISO format (for fallback search)",
                    },
                },
                "required": ["event_id", "title", "start"],
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
    {
        "type": "function",
        "function": {
            "name": "batch_delete_events",
            "description": "Delete multiple events at once. ALWAYS provide title and start for each event along with the ID. For deleting ALL events on a date, use delete_events_by_date instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "event_id": {"type": "string", "description": "Event ID"},
                                "title": {"type": "string", "description": "Event title"},
                                "start": {"type": "string", "description": "Event start time ISO"},
                            },
                            "required": ["event_id", "title", "start"],
                        },
                        "description": "List of events to delete, each with event_id, title, and start",
                    },
                },
                "required": ["events"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_events_by_date",
            "description": "Delete all events on a specific date, optionally filtered by title. BEST tool for 'delete all events tomorrow' or 'delete these tasks for Tuesday'. More reliable than batch_delete_events for recurring events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in ISO format, e.g. 2026-03-18",
                    },
                    "titles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of event titles to delete. If empty, deletes ALL events on that date.",
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_event",
            "description": "Move/reschedule an event to a new date/time. Keeps the same duration unless new_end is specified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to move",
                    },
                    "new_start": {
                        "type": "string",
                        "description": "New start date/time in ISO format, e.g. 2026-03-20T15:00:00",
                    },
                    "new_end": {
                        "type": "string",
                        "description": "New end date/time in ISO format (optional, keeps same duration if not specified)",
                    },
                },
                "required": ["event_id", "new_start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_day_summary",
            "description": "Get a complete summary of a day: events count, free slots, busy hours, and overdue tasks — all in one request. Use this for 'как дела с расписанием?' or 'что у меня сегодня?' type questions.",
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
            "name": "suggest_reschedule",
            "description": "Get overdue tasks and available free slots for rescheduling. Use when there are overdue events and you want to suggest when to reschedule them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": "Date to find free slots on, in ISO format. If not provided, uses today.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_event_color",
            "description": "Set the color/category of an event. Categories: urgent (red), meeting (blue), done (green), in_progress (yellow), learning (purple), personal (gray), health (sage green), work (lavender). Use category name OR color ID (1-11).",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to color",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category name: urgent, meeting, done, in_progress, learning, personal, health, work",
                    },
                    "color": {
                        "type": "string",
                        "description": "Color ID (1-11). Use category instead when possible.",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clone_event",
            "description": "Duplicate an existing event to a new date, keeping the same time, duration, and details. Use for 'copy this event to Wednesday' type requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to clone",
                    },
                    "new_date": {
                        "type": "string",
                        "description": "Target date in ISO format, e.g. 2026-03-20",
                    },
                },
                "required": ["event_id", "new_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_week_stats",
            "description": "Get statistics for the current week: total events, busy hours by category, completed vs overdue counts. Use for 'how was my week?' or 'weekly stats' type questions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_recurring_event",
            "description": "Create a repeating event (daily, weekly, or monthly). Use for 'every Tuesday at 10:00' or 'daily standup' type requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Event title",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start date/time of FIRST occurrence in ISO format",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date/time of first occurrence (optional, defaults to 1h after start)",
                    },
                    "frequency": {
                        "type": "string",
                        "enum": ["daily", "weekly", "monthly"],
                        "description": "How often the event repeats",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of occurrences (optional, infinite if not set)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description (optional)",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category for auto-coloring: urgent, meeting, work, personal, health, learning",
                    },
                },
                "required": ["title", "start", "frequency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_common_free_time",
            "description": "Find the nearest free time slot of a given duration within the next N days. Use for 'when do I have 2 free hours this week?' type questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Required free time duration in minutes",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "How many days ahead to search (default 7)",
                        "default": 7,
                    },
                },
                "required": ["duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_event_done",
            "description": "Mark an event/task as completed. Sets green color and adds 'Done:' prefix. ALWAYS provide title and start along with event_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to mark as done",
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title (for fallback search)",
                    },
                    "start": {
                        "type": "string",
                        "description": "Event start time ISO (for fallback search)",
                    },
                },
                "required": ["event_id", "title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_completed_events",
            "description": "Get list of completed (done) events. Events marked with green color or 'Done:' prefix. Use for 'what have I completed?' type questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "Start date in ISO format (default: 7 days ago)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date in ISO format (default: now)",
                    },
                },
                "required": [],
            },
        },
    },
]
