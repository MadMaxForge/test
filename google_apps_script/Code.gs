// Google Apps Script - Calendar API for Telegram Bot
// Deploy as Web App: Execute as "Me", Access "Anyone"

var SECRET_TOKEN = 'CHANGE_ME_TO_RANDOM_STRING'; // Security token to prevent unauthorized access

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  // Check security token
  if (e.parameter.token !== SECRET_TOKEN) {
    return jsonResponse({error: 'Unauthorized'});
  }

  var action = e.parameter.action;
  var result;

  try {
    switch(action) {
      case 'getEvents':
        result = getEvents(e.parameter.start, e.parameter.end);
        break;
      case 'getTodayEvents':
        result = getTodayEvents();
        break;
      case 'getTomorrowEvents':
        result = getTomorrowEvents();
        break;
      case 'getWeekEvents':
        result = getWeekEvents();
        break;
      case 'getUpcoming':
        result = getUpcomingEvents(parseInt(e.parameter.minutes) || 30);
        break;
      case 'createEvent':
        var data = JSON.parse(e.postData.contents);
        result = createEvent(data);
        break;
      case 'updateEvent':
        var data = JSON.parse(e.postData.contents);
        result = updateEvent(data);
        break;
      case 'deleteEvent':
        var data = JSON.parse(e.postData.contents);
        result = deleteEvent(data);
        break;
      case 'searchEvents':
        result = searchEvents(e.parameter.query, parseInt(e.parameter.days) || 30);
        break;
      case 'getFreeBusy':
        result = getFreeBusy(e.parameter.date);
        break;
      case 'getOverdue':
        result = getOverdueEvents();
        break;
      default:
        result = {error: 'Unknown action: ' + action};
    }
  } catch(err) {
    result = {error: err.toString(), stack: err.stack};
  }

  return jsonResponse(result);
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

// ---- Calendar Functions ----

function getEvents(startStr, endStr) {
  var calendar = CalendarApp.getDefaultCalendar();
  var start = new Date(startStr);
  var end = new Date(endStr);
  var events = calendar.getEvents(start, end);
  return {events: events.map(formatEvent)};
}

function getTodayEvents() {
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  var events = calendar.getEvents(start, end);
  return {events: events.map(formatEvent), date: start.toISOString().split('T')[0]};
}

function getTomorrowEvents() {
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var start = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  var end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2);
  var events = calendar.getEvents(start, end);
  return {events: events.map(formatEvent), date: start.toISOString().split('T')[0]};
}

function getWeekEvents() {
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7);
  var events = calendar.getEvents(start, end);
  return {events: events.map(formatEvent), startDate: start.toISOString().split('T')[0], endDate: end.toISOString().split('T')[0]};
}

function getUpcomingEvents(minutes) {
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var end = new Date(now.getTime() + minutes * 60 * 1000);
  var events = calendar.getEvents(now, end);
  return {events: events.map(formatEvent), withinMinutes: minutes};
}

function getOverdueEvents() {
  // Get events from the past 7 days that might be "tasks" (all-day events or events with specific keywords)
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  var events = calendar.getEvents(weekAgo, now);

  var overdue = events.filter(function(e) {
    // Consider past events as potentially overdue
    return e.getEndTime() < now;
  });

  return {events: overdue.map(formatEvent)};
}

function createEvent(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event;

  if (data.allDay) {
    if (data.endDate) {
      event = calendar.createAllDayEvent(data.title, new Date(data.start), new Date(data.endDate));
    } else {
      event = calendar.createAllDayEvent(data.title, new Date(data.start));
    }
  } else {
    var endTime = data.end || new Date(new Date(data.start).getTime() + 60 * 60 * 1000).toISOString(); // Default 1 hour
    event = calendar.createEvent(data.title, new Date(data.start), new Date(endTime));
  }

  if (data.description) event.setDescription(data.description);
  if (data.location) event.setLocation(data.location);

  // Add reminders
  if (data.reminderMinutes) {
    event.removeAllReminders();
    event.addPopupReminder(data.reminderMinutes);
  }

  return {success: true, event: formatEvent(event)};
}

function updateEvent(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event = calendar.getEventById(data.eventId);

  if (!event) return {error: 'Event not found with ID: ' + data.eventId};

  if (data.title) event.setTitle(data.title);
  if (data.start && data.end) {
    event.setTime(new Date(data.start), new Date(data.end));
  } else if (data.start) {
    // Move event keeping same duration
    var duration = event.getEndTime().getTime() - event.getStartTime().getTime();
    var newStart = new Date(data.start);
    var newEnd = new Date(newStart.getTime() + duration);
    event.setTime(newStart, newEnd);
  }
  if (data.description) event.setDescription(data.description);
  if (data.location) event.setLocation(data.location);

  return {success: true, event: formatEvent(event)};
}

function deleteEvent(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event = calendar.getEventById(data.eventId);

  if (!event) return {error: 'Event not found with ID: ' + data.eventId};

  var title = event.getTitle();
  event.deleteEvent();
  return {success: true, deleted: title};
}

function searchEvents(query, days) {
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var end = new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
  var events = calendar.getEvents(now, end, {search: query});
  return {events: events.map(formatEvent), query: query};
}

function getFreeBusy(dateStr) {
  var calendar = CalendarApp.getDefaultCalendar();
  var date = dateStr ? new Date(dateStr) : new Date();
  var start = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 8, 0);
  var end = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 22, 0);
  var events = calendar.getEvents(start, end);

  var busy = events.map(function(e) {
    return {
      start: e.getStartTime().toISOString(),
      end: e.getEndTime().toISOString(),
      title: e.getTitle()
    };
  });

  // Calculate free slots
  var free = [];
  var lastEnd = start;

  busy.sort(function(a, b) { return new Date(a.start) - new Date(b.start); });

  for (var i = 0; i < busy.length; i++) {
    var eventStart = new Date(busy[i].start);
    if (eventStart > lastEnd) {
      free.push({
        start: lastEnd.toISOString(),
        end: eventStart.toISOString()
      });
    }
    var eventEnd = new Date(busy[i].end);
    if (eventEnd > lastEnd) lastEnd = eventEnd;
  }

  if (lastEnd < end) {
    free.push({
      start: lastEnd.toISOString(),
      end: end.toISOString()
    });
  }

  return {busy: busy, free: free, date: date.toISOString().split('T')[0]};
}

function formatEvent(event) {
  return {
    id: event.getId(),
    title: event.getTitle(),
    start: event.getStartTime().toISOString(),
    end: event.getEndTime().toISOString(),
    description: event.getDescription() || '',
    location: event.getLocation() || '',
    isAllDay: event.isAllDayEvent(),
    color: event.getColor()
  };
}
