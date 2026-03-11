import SwiftUI

struct MessageBubble: View {
    let message: Message

    var body: some View {
        switch message.contentType {
        case .approval:
            if let approval = message.approval {
                ApprovalCard(approval: approval)
            } else {
                textBubble
            }

        case .card:
            if let jobCard = extractJobCard(from: message.cards) {
                JobCard(job: jobCard)
            } else {
                textBubble
            }

        default:
            textBubble
        }
    }

    // MARK: - Text bubble

    private var textBubble: some View {
        HStack(alignment: .bottom, spacing: 8) {
            if isUser { Spacer(minLength: 48) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
                if let agent = message.agentUsed, !isUser {
                    Text(agent.replacingOccurrences(of: "_", with: " ").capitalized)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Text(message.text)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(isUser ? Color.blue : Color(.systemGray5))
                    .foregroundStyle(isUser ? .white : .primary)
                    .clipShape(RoundedRectangle(cornerRadius: 18))

                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            if !isUser { Spacer(minLength: 48) }
        }
    }

    private var isUser: Bool {
        message.source == .ios || message.source == .watch || message.source == .siri
    }

    // MARK: - Card extraction

    private func extractJobCard(from cards: [[String: AnyCodable]]) -> JobCardData? {
        guard let first = cards.first,
              let title = first["title"]?.value as? String,
              let company = first["company"]?.value as? String
        else { return nil }

        return JobCardData(
            id: (first["id"]?.value as? String).flatMap(UUID.init) ?? UUID(),
            title: title,
            company: company,
            location: first["location"]?.value as? String,
            salaryRange: first["salary_range"]?.value as? String,
            matchScore: first["match_score"]?.value as? Int,
            matchReasons: (first["match_reasons"]?.value as? [String]) ?? [],
            concerns: (first["concerns"]?.value as? [String]) ?? [],
            url: first["url"]?.value as? String,
            source: first["source"]?.value as? String
        )
    }
}

#Preview {
    VStack(spacing: 12) {
        MessageBubble(message: Message(
            text: "What's on my schedule today?",
            source: .ios
        ))
        MessageBubble(message: Message(
            text: "You have 3 meetings today. Your first meeting is at 10am with the design team.",
            source: .system,
            agentUsed: "daily_life"
        ))
    }
    .padding()
}
