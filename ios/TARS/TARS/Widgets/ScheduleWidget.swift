import SwiftUI
import WidgetKit

// MARK: - Timeline Entry

struct ScheduleEntry: TimelineEntry {
    let date: Date
    let events: [WidgetCalendarEvent]
    let leaveHomeBy: String?
    let isPlaceholder: Bool

    static var placeholder: ScheduleEntry {
        ScheduleEntry(
            date: .now,
            events: [
                WidgetCalendarEvent(id: "1", title: "Team standup", start: .now, end: .now.addingTimeInterval(1800), location: nil, calendar: "Work", allDay: false),
                WidgetCalendarEvent(id: "2", title: "Lunch with Alex", start: .now.addingTimeInterval(3600), end: .now.addingTimeInterval(7200), location: "Coccia House", calendar: "Personal", allDay: false),
                WidgetCalendarEvent(id: "3", title: "CS 350 Lecture", start: .now.addingTimeInterval(10800), end: .now.addingTimeInterval(14400), location: "Gault Library", calendar: "Classes", allDay: false),
            ],
            leaveHomeBy: "8:15 AM",
            isPlaceholder: true
        )
    }

    static var empty: ScheduleEntry {
        ScheduleEntry(date: .now, events: [], leaveHomeBy: nil, isPlaceholder: false)
    }
}

// MARK: - Timeline Provider

struct ScheduleProvider: TimelineProvider {
    func placeholder(in context: Context) -> ScheduleEntry {
        .placeholder
    }

    func getSnapshot(in context: Context, completion: @escaping (ScheduleEntry) -> Void) {
        if context.isPreview {
            completion(.placeholder)
            return
        }
        Task {
            let entry = await fetchEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ScheduleEntry>) -> Void) {
        Task {
            let entry = await fetchEntry()
            // Refresh every 15 minutes
            let nextUpdate = Calendar.current.date(byAdding: .minute, value: 15, to: .now) ?? .now.addingTimeInterval(900)
            completion(Timeline(entries: [entry], policy: .after(nextUpdate)))
        }
    }

    private func fetchEntry() async -> ScheduleEntry {
        guard let response = await WidgetAPIClient.fetchSchedule() else {
            return .empty
        }
        return ScheduleEntry(
            date: .now,
            events: response.events,
            leaveHomeBy: response.leaveHomeBy,
            isPlaceholder: false
        )
    }
}

// MARK: - Widget View

struct ScheduleWidgetView: View {
    var entry: ScheduleEntry
    @Environment(\.widgetFamily) var family

    var body: some View {
        switch family {
        case .systemSmall:
            smallView
        default:
            mediumView
        }
    }

    // MARK: Small — next event only

    private var smallView: some View {
        VStack(alignment: .leading, spacing: 6) {
            header

            if let event = nextUpcomingEvent {
                VStack(alignment: .leading, spacing: 2) {
                    Text(event.title)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(2)

                    Text(eventTimeString(event))
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    if let location = event.location, !location.isEmpty {
                        Label(location, systemImage: "mappin")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
            } else {
                Spacer()
                Text("No events today")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            }

            Spacer(minLength: 0)

            if let leaveBy = entry.leaveHomeBy {
                HStack(spacing: 3) {
                    Image(systemName: "car.fill")
                        .font(.caption2)
                    Text("Leave by \(leaveBy)")
                        .font(.caption2.weight(.medium))
                }
                .foregroundStyle(.orange)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .containerBackground(.fill.tertiary, for: .widget)
    }

    // MARK: Medium — next 3 events

    private var mediumView: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                header
                Spacer()
                if let leaveBy = entry.leaveHomeBy {
                    HStack(spacing: 3) {
                        Image(systemName: "car.fill")
                            .font(.caption2)
                        Text(leaveBy)
                            .font(.caption2.weight(.medium))
                    }
                    .foregroundStyle(.orange)
                }
            }

            if upcomingEvents.isEmpty {
                Spacer()
                HStack {
                    Spacer()
                    VStack(spacing: 4) {
                        Image(systemName: "calendar")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                        Text("No events today")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                Spacer()
            } else {
                ForEach(upcomingEvents.prefix(3)) { event in
                    eventRow(event)
                }
                if upcomingEvents.count > 3 {
                    Text("+\(upcomingEvents.count - 3) more")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .containerBackground(.fill.tertiary, for: .widget)
    }

    // MARK: Shared components

    private var header: some View {
        HStack(spacing: 4) {
            Image(systemName: "calendar")
                .foregroundStyle(.blue)
                .font(.caption)
            Text("Schedule")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
        }
    }

    private func eventRow(_ event: WidgetCalendarEvent) -> some View {
        HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(calendarColor(for: event.calendar))
                .frame(width: 3)

            VStack(alignment: .leading, spacing: 1) {
                Text(event.title)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                HStack(spacing: 4) {
                    Text(eventTimeString(event))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if let location = event.location, !location.isEmpty {
                        Text("· \(location)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
            }

            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
    }

    // MARK: Helpers

    private var nextUpcomingEvent: WidgetCalendarEvent? {
        upcomingEvents.first
    }

    private var upcomingEvents: [WidgetCalendarEvent] {
        entry.events
            .sorted { $0.start < $1.start }
            .filter { $0.end > .now || $0.allDay }
    }

    private func eventTimeString(_ event: WidgetCalendarEvent) -> String {
        if event.allDay { return "All Day" }
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return "\(formatter.string(from: event.start)) – \(formatter.string(from: event.end))"
    }

    private func calendarColor(for name: String) -> Color {
        let hash = abs(name.hashValue)
        let colors: [Color] = [.blue, .green, .orange, .purple, .pink, .red, .teal, .indigo]
        return colors[hash % colors.count]
    }
}

// MARK: - Widget Definition

struct ScheduleWidget: Widget {
    let kind = "ScheduleWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ScheduleProvider()) { entry in
            ScheduleWidgetView(entry: entry)
        }
        .configurationDisplayName("Today's Schedule")
        .description("Your upcoming events at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

#Preview(as: .systemSmall) {
    ScheduleWidget()
} timeline: {
    ScheduleEntry.placeholder
    ScheduleEntry.empty
}

#Preview(as: .systemMedium) {
    ScheduleWidget()
} timeline: {
    ScheduleEntry.placeholder
    ScheduleEntry.empty
}
