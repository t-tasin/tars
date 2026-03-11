import SwiftUI
import WidgetKit

// MARK: - Timeline Entry

struct OutfitEntry: TimelineEntry {
    let date: Date
    let suggestion: String?
    let reasoning: String?
    let weatherCondition: String?
    let tempHigh: Int?
    let tempLow: Int?
    let isPlaceholder: Bool

    static var placeholder: OutfitEntry {
        OutfitEntry(
            date: .now,
            suggestion: "Navy chinos, white oxford shirt, light gray sweater. Brown leather boots.",
            reasoning: "Partly cloudy and cool — layering keeps you comfortable indoors and out.",
            weatherCondition: "Partly Cloudy",
            tempHigh: 58,
            tempLow: 42,
            isPlaceholder: true
        )
    }

    static var empty: OutfitEntry {
        OutfitEntry(date: .now, suggestion: nil, reasoning: nil, weatherCondition: nil, tempHigh: nil, tempLow: nil, isPlaceholder: false)
    }
}

// MARK: - Timeline Provider

struct OutfitProvider: TimelineProvider {
    func placeholder(in context: Context) -> OutfitEntry {
        .placeholder
    }

    func getSnapshot(in context: Context, completion: @escaping (OutfitEntry) -> Void) {
        if context.isPreview {
            completion(.placeholder)
            return
        }
        Task {
            let entry = await fetchEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<OutfitEntry>) -> Void) {
        Task {
            let entry = await fetchEntry()
            // Refresh every 2 hours — outfit suggestion doesn't change often
            let nextUpdate = Calendar.current.date(byAdding: .hour, value: 2, to: .now) ?? .now.addingTimeInterval(7200)
            completion(Timeline(entries: [entry], policy: .after(nextUpdate)))
        }
    }

    private func fetchEntry() async -> OutfitEntry {
        guard let response = await WidgetAPIClient.fetchOutfit() else {
            return .empty
        }
        return OutfitEntry(
            date: .now,
            suggestion: response.suggestion,
            reasoning: response.reasoning,
            weatherCondition: response.weather?.condition,
            tempHigh: response.weather?.high,
            tempLow: response.weather?.low,
            isPlaceholder: false
        )
    }
}

// MARK: - Widget View

struct OutfitWidgetView: View {
    var entry: OutfitEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                HStack(spacing: 4) {
                    Image(systemName: "tshirt.fill")
                        .foregroundStyle(.purple)
                        .font(.caption)
                    Text("Today's Outfit")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }

                Spacer()

                if let condition = entry.weatherCondition {
                    HStack(spacing: 3) {
                        Image(systemName: weatherIcon(for: condition))
                            .font(.caption)
                            .foregroundStyle(.orange)
                        if let high = entry.tempHigh, let low = entry.tempLow {
                            Text("\(high)°/\(low)°")
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            if let suggestion = entry.suggestion {
                Text(suggestion)
                    .font(.caption.weight(.medium))
                    .lineLimit(3)

                if let reasoning = entry.reasoning {
                    Text(reasoning)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            } else {
                Spacer()
                HStack {
                    Spacer()
                    VStack(spacing: 4) {
                        Image(systemName: "tshirt")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                        Text("No outfit suggestion yet")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                Spacer()
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .containerBackground(.fill.tertiary, for: .widget)
    }

    private func weatherIcon(for condition: String) -> String {
        let lower = condition.lowercased()
        if lower.contains("sun") || lower.contains("clear") { return "sun.max.fill" }
        if lower.contains("cloud") && lower.contains("part") { return "cloud.sun.fill" }
        if lower.contains("cloud") { return "cloud.fill" }
        if lower.contains("rain") || lower.contains("shower") { return "cloud.rain.fill" }
        if lower.contains("snow") { return "cloud.snow.fill" }
        if lower.contains("thunder") || lower.contains("storm") { return "cloud.bolt.fill" }
        if lower.contains("fog") || lower.contains("mist") { return "cloud.fog.fill" }
        if lower.contains("wind") { return "wind" }
        return "cloud.sun.fill"
    }
}

// MARK: - Widget Definition

struct OutfitWidget: Widget {
    let kind = "OutfitWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: OutfitProvider()) { entry in
            OutfitWidgetView(entry: entry)
        }
        .configurationDisplayName("Today's Outfit")
        .description("Outfit suggestion based on weather and your schedule.")
        .supportedFamilies([.systemMedium])
    }
}

#Preview(as: .systemMedium) {
    OutfitWidget()
} timeline: {
    OutfitEntry.placeholder
    OutfitEntry.empty
}
