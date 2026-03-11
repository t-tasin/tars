import SwiftUI

struct WatchApprovalView: View {
    let approval: WatchApproval
    @EnvironmentObject private var connectivity: WatchConnectivityManager

    @State private var isProcessing = false
    @State private var decisionMade: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header
                previewSection
                expiryInfo
                actionButtons
            }
            .padding(.horizontal)
        }
        .navigationTitle("Review")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: approval.actionIcon)
                    .foregroundStyle(Color(approval.riskColor))
                Text(approval.actionType.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text(approval.title)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)

            if approval.riskTier == "tier3_escalation" {
                Label("High Risk", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.red)
            }
        }
    }

    // MARK: - Preview

    private var previewSection: some View {
        Group {
            if !approval.previewSummary.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Details")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(approval.previewSummary)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    // MARK: - Expiry

    private var expiryInfo: some View {
        Group {
            if approval.isExpired {
                Label("Expired", systemImage: "clock.badge.xmark")
                    .font(.caption2)
                    .foregroundStyle(.red)
            } else {
                let remaining = approval.expiresAt.timeIntervalSinceNow
                let minutes = Int(remaining / 60)
                Label(
                    minutes > 60 ? "\(minutes / 60)h \(minutes % 60)m left" : "\(minutes)m left",
                    systemImage: "clock"
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Actions

    @ViewBuilder
    private var actionButtons: some View {
        if let decision = decisionMade {
            HStack {
                Spacer()
                Label(
                    decision == "approved" ? "Approved" : "Rejected",
                    systemImage: decision == "approved" ? "checkmark.circle.fill" : "xmark.circle.fill"
                )
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(decision == "approved" ? .green : .red)
                Spacer()
            }
            .padding(.top, 4)
        } else if approval.isExpired {
            Text("This approval has expired")
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)
        } else {
            VStack(spacing: 8) {
                Button {
                    handleDecision("approved")
                } label: {
                    HStack {
                        Image(systemName: "checkmark")
                        Text("Approve")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
                .disabled(isProcessing)

                Button(role: .destructive) {
                    handleDecision("rejected")
                } label: {
                    HStack {
                        Image(systemName: "xmark")
                        Text("Reject")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(isProcessing)
            }
            .padding(.top, 4)

            if isProcessing {
                ProgressView()
                    .frame(maxWidth: .infinity)
            }
        }
    }

    // MARK: - Handle decision

    private func handleDecision(_ decision: String) {
        isProcessing = true
        connectivity.sendApprovalDecision(approvalId: approval.id, decision: decision)
        // Optimistic UI — mark as decided after short delay
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            decisionMade = decision
            isProcessing = false
        }
    }
}

// MARK: - Color extension for string-based colors

private extension Color {
    init(_ name: String) {
        switch name {
        case "red": self = .red
        case "orange": self = .orange
        case "green": self = .green
        default: self = .blue
        }
    }
}

#Preview {
    NavigationStack {
        WatchApprovalView(approval: WatchApproval(
            id: UUID(),
            actionType: "send_email",
            riskTier: "tier2_approval",
            title: "Send email to Prof. Sadigh",
            previewSummary: "To: sadigh@cs.stanford.edu\nSubject: Following up on research opportunity",
            expiresAt: Date().addingTimeInterval(3600),
            createdAt: Date()
        ))
        .environmentObject(WatchConnectivityManager.shared)
    }
}
