// Google Apps Script - Calendar API for Telegram Bot
// Deploy as Web App: Execute as "Me", Access "Anyone"

var SECRET_TOKEN = 'CHANGE_ME_TO_RANDOM_STRING'; // Security token to prevent unauthorized access

// Color map for categories
var COLOR_MAP = {
  'urgent': '11',      // Red - urgent/deadlines
  'meeting': '9',      // Blue - meetings
  'done': '10',        // Green - completed
  'in_progress': '5',  // Yellow - in progress
  'learning': '3',     // Purple - learning
  'personal': '8',     // Gray - personal/rest
  'health': '2',       // Green (sage) - health
  'work': '1',         // Lavender - work
};

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function handleRequest(e) {
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
      case 'createRecurringEvent':
        var data = JSON.parse(e.postData.contents);
        result = createRecurringEvent(data);
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
      case 'setEventColor':
        var data = JSON.parse(e.postData.contents);
        result = setEventColor(data);
        break;
      case 'cloneEvent':
        var data = JSON.parse(e.postData.contents);
        result = cloneEvent(data);
        break;
      case 'markEventDone':
        var data = JSON.parse(e.postData.contents);
        result = markEventDone(data);
        break;
      case 'getCompletedEvents':
        result = getCompletedEvents(e.parameter.start, e.parameter.end);
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
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  var events = calendar.getEvents(weekAgo, now);

  var overdue = events.filter(function(e) {
    // Past events that are NOT marked done (green/10)
    return e.getEndTime() < now && e.getColor() !== '10' && e.getTitle().indexOf('Done:') !== 0;
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
    var endTime = data.end || new Date(new Date(data.start).getTime() + 60 * 60 * 1000).toISOString();
    event = calendar.createEvent(data.title, new Date(data.start), new Date(endTime));
  }

  if (data.description) event.setDescription(data.description);
  if (data.location) event.setLocation(data.location);

  // Set color based on category or explicit color
  if (data.color) {
    event.setColor(data.color);
  } else if (data.category && COLOR_MAP[data.category]) {
    event.setColor(COLOR_MAP[data.category]);
  }

  if (data.reminderMinutes) {
    event.removeAllReminders();
    event.addPopupReminder(data.reminderMinutes);
  }

  return {success: true, event: formatEvent(event)};
}

function createRecurringEvent(data) {
  var calendar = CalendarApp.getDefaultCalendar();

  var startTime = new Date(data.start);
  var endTime = data.end ? new Date(data.end) : new Date(startTime.getTime() + 60 * 60 * 1000);

  var freq = (data.frequency || 'weekly').toLowerCase();
  var rule;

  if (freq === 'daily') {
    rule = CalendarApp.newRecurrence().addDailyRule();
  } else if (freq === 'weekly') {
    rule = CalendarApp.newRecurrence().addWeeklyRule();
  } else if (freq === 'monthly') {
    rule = CalendarApp.newRecurrence().addMonthlyRule();
  } else {
    rule = CalendarApp.newRecurrence().addWeeklyRule();
  }

  if (data.count) {
    // Recreate with count
    if (freq === 'daily') rule = CalendarApp.newRecurrence().addDailyRule().times(data.count);
    else if (freq === 'monthly') rule = CalendarApp.newRecurrence().addMonthlyRule().times(data.count);
    else rule = CalendarApp.newRecurrence().addWeeklyRule().times(data.count);
  }

  var event = calendar.createEventSeries(data.title, startTime, endTime, rule);

  if (data.description) event.setDescription(data.description);
  if (data.location) event.setLocation(data.location);
  if (data.color) event.setColor(data.color);
  else if (data.category && COLOR_MAP[data.category]) event.setColor(COLOR_MAP[data.category]);

  return {success: true, title: data.title, frequency: freq, start: startTime.toISOString()};
}

function updateEvent(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event = calendar.getEventById(data.eventId);

  if (!event) return {error: 'Event not found with ID: ' + data.eventId};

  if (data.title) event.setTitle(data.title);
  if (data.start && data.end) {
    event.setTime(new Date(data.start), new Date(data.end));
  } else if (data.start) {
    var duration = event.getEndTime().getTime() - event.getStartTime().getTime();
    var newStart = new Date(data.start);
    var newEnd = new Date(newStart.getTime() + duration);
    event.setTime(newStart, newEnd);
  }
  if (data.description) event.setDescription(data.description);
  if (data.location) event.setLocation(data.location);
  if (data.color) event.setColor(data.color);

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

function setEventColor(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event = calendar.getEventById(data.eventId);

  if (!event) return {error: 'Event not found with ID: ' + data.eventId};

  var colorId = data.color;
  if (data.category && COLOR_MAP[data.category]) {
    colorId = COLOR_MAP[data.category];
  }

  event.setColor(colorId);
  return {success: true, event: formatEvent(event), colorSet: colorId};
}

function cloneEvent(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event = calendar.getEventById(data.eventId);

  if (!event) return {error: 'Event not found with ID: ' + data.eventId};

  var newDate = new Date(data.newDate);
  var originalStart = event.getStartTime();
  var originalEnd = event.getEndTime();
  var duration = originalEnd.getTime() - originalStart.getTime();

  var newStart = new Date(newDate.getFullYear(), newDate.getMonth(), newDate.getDate(),
                          originalStart.getHours(), originalStart.getMinutes());
  var newEnd = new Date(newStart.getTime() + duration);

  var newEvent;
  if (event.isAllDayEvent()) {
    newEvent = calendar.createAllDayEvent(event.getTitle(), newStart);
  } else {
    newEvent = calendar.createEvent(event.getTitle(), newStart, newEnd);
  }

  if (event.getDescription()) newEvent.setDescription(event.getDescription());
  if (event.getLocation()) newEvent.setLocation(event.getLocation());
  if (event.getColor()) newEvent.setColor(event.getColor());

  return {success: true, original: formatEvent(event), clone: formatEvent(newEvent)};
}

function markEventDone(data) {
  var calendar = CalendarApp.getDefaultCalendar();
  var event = calendar.getEventById(data.eventId);

  if (!event) return {error: 'Event not found with ID: ' + data.eventId};

  event.setColor('10');
  var title = event.getTitle();
  if (title.indexOf('Done:') !== 0) {
    event.setTitle('Done: ' + title);
  }

  return {success: true, event: formatEvent(event)};
}

function getCompletedEvents(startStr, endStr) {
  var calendar = CalendarApp.getDefaultCalendar();
  var now = new Date();
  var start = startStr ? new Date(startStr) : new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  var end = endStr ? new Date(endStr) : now;
  var events = calendar.getEvents(start, end);

  var completed = events.filter(function(e) {
    return e.getColor() === '10' || e.getTitle().indexOf('Done:') === 0;
  });

  return {events: completed.map(formatEvent), count: completed.length};
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
    color: event.getColor(),
    isDone: event.getColor() === '10' || event.getTitle().indexOf('Done:') === 0
  };
}
