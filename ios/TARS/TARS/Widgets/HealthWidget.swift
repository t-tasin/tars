import SwiftUI
import WidgetKit

// MARK: - Timeline Entry

struct HealthEntry: TimelineEntry {
    let date: Date
    let sleepHours: Double?
    let steps: Int?
    let heartRate: Int?
    let isPlaceholder: Bool

    static var placeholder: HealthEntry {
        HealthEntry(date: .now, sleepHours: 7.5, steps: 6234, heartRate: 68, isPlaceholder: true)
    }

    static var empty: HealthEntry {
        HealthEntry(date: .now, sleepHours: nil, steps: nil, heartRate: nil, isPlaceholder: false)
    }
}

// MARK: - Timeline Provider

struct HealthProvider: TimelineProvider {
    func placeholder(in context: Context) -> HealthEntry {
        .placeholder
    }

    func getSnapshot(in context: Context, completion: @escaping (HealthEntry) -> Void) {
        if context.isPreview {
            completion(.placeholder)
            return
        }
        Task {
            let entry = await fetchEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<HealthEntry>) -> Void) {
        Task {
            let entry = await fetchEntry()
            // Refresh every 30 minutes — health data doesn't change rapidly
            let nextUpdate = Calendar.current.date(byAdding: .minute, value: 30, to: .now) ?? .now.addingTimeInterval(1800)
            completion(Timeline(entries: [entry], policy: .after(nextUpdate)))
        }
    }

    private func fetchEntry() async -> HealthEntry {
        guard let response = await WidgetAPIClient.fetchHealthSummary() else {
            return .empty
        }
        return HealthEntry(
            date: .now,
            sleepHours: response.sleepHours,
            steps: response.steps,
            heartRate: response.heartRate,
            isPlaceholder: false
        )
    }
}

// MARK: - Widget View

struct HealthWidgetView: View {
    var entry: HealthEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 4) {
                Image(systemName: "heart.fill")
                    .foregroundStyle(.red)
                    .font(.caption)
                Text("Health")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }

            if hasData {
                VStack(alignment: .leading, spacing: 6) {
                    if let sleep = entry.sleepHours {
                        statRow(
                            icon: "moon.zzz.fill",
                            color: .indigo,
                            label: "Sleep",
                            value: formatSleep(sleep)
                        )
                    }

                    if let steps = entry.steps {
                        statRow(
                            icon: "figure.walk",
                            color: .green,
                            label: "Steps",
                            value: formatSteps(steps)
                        )
                    }

                    if let hr = entry.heartRate {
                        statRow(
                            icon: "heart.fill",
                            color: .red,
                            label: "Heart Rate",
                            value: "\(hr) bpm"
                        )
                    }
                }
            } else {
                Spacer()
                VStack(spacing: 4) {
                    Image(systemName: "heart.text.clipboard")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                    Text("No health data")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
                Spacer()
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .containerBackground(.fill.tertiary, for: .widget)
    }

    private var hasData: Bool {
        entry.sleepHours != nil || entry.steps != nil || entry.heartRate != nil
    }

    private func statRow(icon: String, color: Color, label: String, value: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(color)
                .frame(width: 16)

            VStack(alignment: .leading, spacing: 0) {
                Text(value)
                    .font(.caption.weight(.semibold))
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func formatSleep(_ hours: Double) -> String {
        let h = Int(hours)
        let m = Int((hours - Double(h)) * 60)
        return m > 0 ? "\(h)h \(m)m" : "\(h)h"
    }

    private func formatSteps(_ steps: Int) -> String {
        if steps >= 1000 {
            let k = Double(steps) / 1000.0
            return String(format: "%.1fk", k)
        }
        return "\(steps)"
    }
}

// MARK: - Widget Definition

struct HealthWidget: Widget {
    let kind = "HealthWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HealthProvider()) { entry in
            HealthWidgetView(entry: entry)
        }
        .configurationDisplayName("Health Summary")
        .description("Last night's sleep and today's steps at a glance.")
        .supportedFamilies([.systemSmall])
    }
}

#Preview(as: .systemSmall) {
    HealthWidget()
} timeline: {
    HealthEntry.placeholder
    HealthEntry.empty
}
