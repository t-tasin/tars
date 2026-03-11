import SwiftUI

struct WatchMainView: View {
    @EnvironmentObject private var connectivity: WatchConnectivityManager

    var body: some View {
        NavigationStack {
            List {
                nextEventSection
                approvalsSection
                scheduleSection
            }
            .navigationTitle("T.A.R.S.")
            .refreshable {
                connectivity.requestSync()
            }
        }
    }

    // MARK: - Next Event

    @ViewBuilder
    private var nextEventSection: some View {
        Section {
            if let event = connectivity.nextEvent {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Image(systemName: "calendar")
                            .foregroundStyle(.orange)
                            .font(.caption)
                        if event.isNow {
                            Text("NOW")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(.green)
                        } else {
                            Text(relativeTime(for: event))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Text(event.title)
                        .font(.headline)
                        .lineLimit(2)
                    Text(event.timeRange)
                        .font(.caption)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                    if let location = event.location {
                        Label(location, systemImage: "location")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                .padding(.vertical, 2)
            } else {
                HStack {
                    Image(systemName: "checkmark.circle")
                        .foregroundStyle(.green)
                    Text("No upcoming events")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

            if let leaveBy = connectivity.leaveHomeBy {
                HStack(spacing: 4) {
                    Image(systemName: "car")
                        .foregroundStyle(.orange)
                        .font(.caption)
                    Text("Leave by \(leaveBy)")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
        } header: {
            Text("Next Up")
        }
    }

    // MARK: - Approvals

    @ViewBuilder
    private var approvalsSection: some View {
        Section {
            NavigationLink {
                WatchApprovalListView()
                    .environmentObject(connectivity)
            } label: {
                HStack {
                    Image(systemName: "checkmark.circle.badge.questionmark")
                        .foregroundStyle(connectivity.pendingCount > 0 ? .orange : .secondary)
                    Text("Pending Approvals")
                        .font(.subheadline)
                    Spacer()
                    if connectivity.pendingCount > 0 {
                        Text("\(connectivity.pendingCount)")
                            .font(.caption.weight(.bold))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.orange, in: Capsule())
                    }
                }
            }
        } header: {
            Text("Actions")
        }
    }

    // MARK: - Schedule

    @ViewBuilder
    private var scheduleSection: some View {
        Section {
            if connectivity.events.isEmpty {
                Text("No events today")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(connectivity.events) { event in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(event.title)
                            .font(.subheadline)
                            .lineLimit(1)
                        Text(event.timeRange)
                            .font(.caption2)
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 1)
                }
            }
        } header: {
            Text("Today's Schedule")
        }
    }

    // MARK: - Helpers

    private func relativeTime(for event: WatchEvent) -> String {
        let minutes = event.minutesUntilStart
        if minutes < 0 { return "Started" }
        if minutes < 1 { return "Now" }
        if minutes < 60 { return "in \(minutes)m" }
        let hours = minutes / 60
        let remainingMinutes = minutes % 60
        if remainingMinutes == 0 { return "in \(hours)h" }
        return "in \(hours)h \(remainingMinutes)m"
    }
}

// MARK: - Approval List View (drill-down)

struct WatchApprovalListView: View {
    @EnvironmentObject private var connectivity: WatchConnectivityManager

    var body: some View {
        List {
            if connectivity.approvals.isEmpty {
                Text("No pending approvals")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(connectivity.approvals) { approval in
                    NavigationLink {
                        WatchApprovalView(approval: approval)
                            .environmentObject(connectivity)
                    } label: {
                        HStack {
                            Image(systemName: approval.actionIcon)
                                .foregroundStyle(Color(approval.riskColor))
                                .font(.caption)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(approval.title)
                                    .font(.subheadline)
                                    .lineLimit(2)
                                Text(approval.actionType.replacingOccurrences(of: "_", with: " "))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Approvals")
    }
}

#Preview {
    WatchMainView()
        .environmentObject(WatchConnectivityManager.shared)
}
