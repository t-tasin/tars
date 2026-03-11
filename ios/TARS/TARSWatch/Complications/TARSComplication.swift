import SwiftUI
import WidgetKit

// MARK: - Timeline Provider

struct TARSTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> TARSTimelineEntry {
        TARSTimelineEntry(
            date: Date(),
            nextEventTitle: "Team Meeting",
            nextEventTime: Date().addingTimeInterval(3600),
            pendingApprovals: 2,
            leaveHomeBy: "8:15 AM"
        )
    }

    func getSnapshot(in context: Context, completion: @escaping (TARSTimelineEntry) -> Void) {
        let entry = currentEntry()
        completion(entry)
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<TARSTimelineEntry>) -> Void) {
        let entry = currentEntry()
        // Refresh every 15 minutes
        let nextUpdate = Calendar.current.date(byAdding: .minute, value: 15, to: Date()) ?? Date()
        let timeline = Timeline(entries: [entry], policy: .after(nextUpdate))
        completion(timeline)
    }

    private func currentEntry() -> TARSTimelineEntry {
        let defaults = UserDefaults(suiteName: "group.com.tasin.tars") ?? .standard

        let nextTitle = defaults.string(forKey: "complication_nextEventTitle")
        let nextTimeInterval = defaults.double(forKey: "complication_nextEventTime")
        let pendingCount = defaults.integer(forKey: "complication_pendingApprovals")
        let leaveBy = defaults.string(forKey: "complication_leaveHomeBy")

        return TARSTimelineEntry(
            date: Date(),
            nextEventTitle: nextTitle,
            nextEventTime: nextTimeInterval > 0 ? Date(timeIntervalSince1970: nextTimeInterval) : nil,
            pendingApprovals: pendingCount,
            leaveHomeBy: leaveBy
        )
    }
}

// MARK: - Timeline Entry

struct TARSTimelineEntry: TimelineEntry {
    let date: Date
    let nextEventTitle: String?
    let nextEventTime: Date?
    let pendingApprovals: Int
    let leaveHomeBy: String?
}

// MARK: - Complication Views

struct TARSComplicationView: View {
    @Environment(\.widgetFamily) var family
    let entry: TARSTimelineEntry

    var body: some View {
        switch family {
        case .accessoryCircular:
            circularView
        case .accessoryCorner:
            cornerView
        case .accessoryRectangular:
            rectangularView
        case .accessoryInline:
            inlineView
        default:
            circularView
        }
    }

    // MARK: - Circular (small round complication)

    private var circularView: some View {
        ZStack {
            AccessoryWidgetBackground()
            VStack(spacing: 1) {
                if entry.pendingApprovals > 0 {
                    Image(systemName: "checkmark.circle")
                        .font(.caption)
                    Text("\(entry.pendingApprovals)")
                        .font(.title3.weight(.bold))
                        .monospacedDigit()
                } else {
                    Image(systemName: "calendar")
                        .font(.caption)
                    if let time = entry.nextEventTime {
                        Text(time, style: .time)
                            .font(.caption2)
                            .monospacedDigit()
                    } else {
                        Text("--")
                            .font(.caption2)
                    }
                }
            }
        }
    }

    // MARK: - Corner

    private var cornerView: some View {
        VStack {
            if entry.pendingApprovals > 0 {
                Text("\(entry.pendingApprovals)")
                    .font(.title3.weight(.bold))
                    .monospacedDigit()
                    .widgetLabel {
                        Text("approvals")
                    }
            } else if let time = entry.nextEventTime {
                Text(time, style: .time)
                    .font(.caption)
                    .monospacedDigit()
                    .widgetLabel {
                        Text(entry.nextEventTitle ?? "Next")
                    }
            } else {
                Image(systemName: "checkmark.circle")
                    .widgetLabel {
                        Text("T.A.R.S.")
                    }
            }
        }
    }

    // MARK: - Rectangular (most informative)

    private var rectangularView: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Image(systemName: "sparkles")
                    .font(.caption2)
                Text("T.A.R.S.")
                    .font(.caption2.weight(.semibold))
                Spacer()
                if entry.pendingApprovals > 0 {
                    HStack(spacing: 2) {
                        Image(systemName: "exclamationmark.circle.fill")
                            .font(.caption2)
                        Text("\(entry.pendingApprovals)")
                            .font(.caption2.weight(.bold))
                            .monospacedDigit()
                    }
                }
            }

            if let eventTitle = entry.nextEventTitle, let eventTime = entry.nextEventTime {
                Text(eventTitle)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                HStack(spacing: 4) {
                    Image(systemName: "clock")
                        .font(.system(size: 8))
                    Text(eventTime, style: .time)
                        .font(.caption2)
                        .monospacedDigit()
                    if let leaveBy = entry.leaveHomeBy {
                        Text("| Leave \(leaveBy)")
                            .font(.caption2)
                            .lineLimit(1)
                    }
                }
                .foregroundStyle(.secondary)
            } else {
                Text("No upcoming events")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Inline (single line on watch face)

    private var inlineView: some View {
        Group {
            if entry.pendingApprovals > 0 {
                Label("\(entry.pendingApprovals) approvals", systemImage: "checkmark.circle")
            } else if let title = entry.nextEventTitle, let time = entry.nextEventTime {
                Label("\(shortTime(time)) \(title)", systemImage: "calendar")
            } else {
                Label("No events", systemImage: "checkmark.circle")
            }
        }
        .font(.caption)
    }

    private func shortTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm"
        return formatter.string(from: date)
    }
}

// MARK: - Widget Configuration

struct TARSComplication: Widget {
    let kind: String = "TARSComplication"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: TARSTimelineProvider()) { entry in
            TARSComplicationView(entry: entry)
        }
        .configurationDisplayName("T.A.R.S.")
        .description("Next event and pending approvals")
        .supportedFamilies([
            .accessoryCircular,
            .accessoryCorner,
            .accessoryRectangular,
            .accessoryInline,
        ])
    }
}

// MARK: - Previews

#Preview("Circular", as: .accessoryCircular) {
    TARSComplication()
} timeline: {
    TARSTimelineEntry(
        date: Date(),
        nextEventTitle: "Team Standup",
        nextEventTime: Date().addingTimeInterval(1800),
        pendingApprovals: 3,
        leaveHomeBy: "8:15 AM"
    )
}

#Preview("Rectangular", as: .accessoryRectangular) {
    TARSComplication()
} timeline: {
    TARSTimelineEntry(
        date: Date(),
        nextEventTitle: "CS 229 Lecture",
        nextEventTime: Date().addingTimeInterval(3600),
        pendingApprovals: 1,
        leaveHomeBy: "8:15 AM"
    )
}

#Preview("Inline", as: .accessoryInline) {
    TARSComplication()
} timeline: {
    TARSTimelineEntry(
        date: Date(),
        nextEventTitle: "Office Hours",
        nextEventTime: Date().addingTimeInterval(7200),
        pendingApprovals: 0,
        leaveHomeBy: nil
    )
}
