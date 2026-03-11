import SwiftUI

// Data extracted from card dictionaries for display
struct JobCardData: Sendable {
    let id: UUID
    let title: String
    let company: String
    let location: String?
    let salaryRange: String?
    let matchScore: Int?
    let matchReasons: [String]
    let concerns: [String]
    let url: String?
    let source: String?
}

struct JobCard: View {
    let job: JobCardData
    @State private var actionTaken: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(job.title)
                        .font(.headline)
                    Text(job.company)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if let score = job.matchScore {
                    scoreView(score)
                }
            }

            // Location & salary
            HStack(spacing: 12) {
                if let location = job.location {
                    Label(location, systemImage: "mappin")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let salary = job.salaryRange {
                    Label(salary, systemImage: "dollarsign")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            // Source
            if let source = job.source {
                Text(source.capitalized)
                    .font(.caption2.weight(.medium))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.blue.opacity(0.1))
                    .foregroundStyle(.blue)
                    .clipShape(Capsule())
            }

            // Match reasons
            if !job.matchReasons.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Why it matches")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.green)
                    ForEach(job.matchReasons, id: \.self) { reason in
                        Label(reason, systemImage: "checkmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            // Concerns
            if !job.concerns.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Concerns")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.orange)
                    ForEach(job.concerns, id: \.self) { concern in
                        Label(concern, systemImage: "exclamationmark.triangle.fill")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            // Actions
            if let action = actionTaken {
                Label(action.capitalized, systemImage: actionIcon(action))
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            } else {
                HStack(spacing: 12) {
                    Button {
                        actionTaken = "skipped"
                    } label: {
                        Text("Skip")
                            .font(.subheadline.weight(.medium))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                    }
                    .buttonStyle(.bordered)
                    .tint(.secondary)

                    Button {
                        actionTaken = "saved"
                    } label: {
                        Label("Save", systemImage: "bookmark")
                            .font(.subheadline.weight(.medium))
                            .padding(.vertical, 8)
                    }
                    .buttonStyle(.bordered)
                    .tint(.blue)

                    Button {
                        actionTaken = "applying"
                    } label: {
                        Label("Apply", systemImage: "paperplane")
                            .font(.subheadline.weight(.medium))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.green)
                }
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(Color(.systemGray4), lineWidth: 0.5)
        )
    }

    // MARK: - Score badge

    private func scoreView(_ score: Int) -> some View {
        VStack(spacing: 2) {
            Text("\(score)")
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(scoreColor(score))
            Text("match")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(width: 48, height: 48)
        .background(
            Circle()
                .strokeBorder(scoreColor(score).opacity(0.3), lineWidth: 2)
        )
    }

    private func scoreColor(_ score: Int) -> Color {
        switch score {
        case 70...: return .green
        case 40...: return .orange
        default: return .red
        }
    }

    private func actionIcon(_ action: String) -> String {
        switch action {
        case "saved": return "bookmark.fill"
        case "applying": return "paperplane.fill"
        case "skipped": return "forward.fill"
        default: return "checkmark"
        }
    }
}

#Preview {
    JobCard(job: JobCardData(
        id: UUID(),
        title: "iOS Engineer",
        company: "Acme Corp",
        location: "San Francisco, CA",
        salaryRange: "$150k - $200k",
        matchScore: 85,
        matchReasons: ["Strong SwiftUI experience", "Matches salary expectations"],
        concerns: ["Requires 5+ years experience"],
        url: "https://example.com/job/123",
        source: "linkedin"
    ))
    .padding()
}
