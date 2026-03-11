import SwiftUI
import WidgetKit

// MARK: - Timeline Entry

struct ApprovalsEntry: TimelineEntry {
    let date: Date
    let pendingCount: Int
    let oldestTitle: String?
    let isPlaceholder: Bool

    static var placeholder: ApprovalsEntry {
        ApprovalsEntry(date: .now, pendingCount: 3, oldestTitle: "Send email to Prof. Sadigh", isPlaceholder: true)
    }

    static var empty: ApprovalsEntry {
        ApprovalsEntry(date: .now, pendingCount: 0, oldestTitle: nil, isPlaceholder: false)
    }
}

// MARK: - Timeline Provider

struct ApprovalsProvider: TimelineProvider {
    func placeholder(in context: Context) -> ApprovalsEntry {
        .placeholder
    }

    func getSnapshot(in context: Context, completion: @escaping (ApprovalsEntry) -> Void) {
        if context.isPreview {
            completion(.placeholder)
            return
        }
        Task {
            let entry = await fetchEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ApprovalsEntry>) -> Void) {
        Task {
            let entry = await fetchEntry()
            // Refresh every 5 minutes — approvals are time-sensitive
            let nextUpdate = Calendar.current.date(byAdding: .minute, value: 5, to: .now) ?? .now.addingTimeInterval(300)
            completion(Timeline(entries: [entry], policy: .after(nextUpdate)))
        }
    }

    private func fetchEntry() async -> ApprovalsEntry {
        guard let response = await WidgetAPIClient.fetchPendingApprovals() else {
            return .empty
        }
        let oldest = response.approvals
            .sorted { $0.expiresAt < $1.expiresAt }
            .first
        return ApprovalsEntry(
            date: .now,
            pendingCount: response.pendingCount,
            oldestTitle: oldest?.title,
            isPlaceholder: false
        )
    }
}

// MARK: - Widget View

struct ApprovalsWidgetView: View {
    var entry: ApprovalsEntry

    var body: some View {
        VStack(spacing: 6) {
            if entry.pendingCount == 0 {
                emptyState
            } else {
                badgeState
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .containerBackground(.fill.tertiary, for: .widget)
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "checkmark.circle.fill")
                .font(.title)
                .foregroundStyle(.green)

            Text("All Clear")
                .font(.caption.weight(.semibold))

            Text("No pending approvals")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private var badgeState: some View {
        VStack(spacing: 4) {
            ZStack {
                Circle()
                    .fill(.orange.gradient)
                    .frame(width: 44, height: 44)

                Text("\(entry.pendingCount)")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
            }

            Text("Pending")
                .font(.caption.weight(.semibold))

            if let title = entry.oldestTitle {
                Text(title)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(.horizontal, 4)
    }
}

// MARK: - Widget Definition

struct ApprovalsWidget: Widget {
    let kind = "ApprovalsWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ApprovalsProvider()) { entry in
            ApprovalsWidgetView(entry: entry)
        }
        .configurationDisplayName("Pending Approvals")
        .description("See how many actions are waiting for your approval.")
        .supportedFamilies([.systemSmall])
    }
}

#Preview(as: .systemSmall) {
    ApprovalsWidget()
} timeline: {
    ApprovalsEntry.placeholder
    ApprovalsEntry.empty
}
