import SwiftUI

struct ApprovalCard: View {
    let approval: ApprovalPreview
    @EnvironmentObject private var viewModel: ChatViewModel
    @State private var isProcessing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack {
                Image(systemName: iconName)
                    .foregroundStyle(tierColor)
                Text(approval.actionType.replacingOccurrences(of: "_", with: " ").uppercased())
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(tierColor)
                Spacer()
                Image(systemName: "shield.checkered")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // Title
            Text(approval.title)
                .font(.headline)

            // Preview details
            previewContent

            // Action buttons
            HStack(spacing: 12) {
                Button {
                    isProcessing = true
                    viewModel.reject(id: approval.approvalId)
                } label: {
                    Label("Reject", systemImage: "xmark")
                        .font(.subheadline.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.bordered)
                .tint(.red)

                Button {
                    // TODO: Present edit sheet
                } label: {
                    Label("Edit", systemImage: "pencil")
                        .font(.subheadline.weight(.medium))
                        .padding(.vertical, 8)
                }
                .buttonStyle(.bordered)
                .tint(.orange)

                Button {
                    isProcessing = true
                    viewModel.approve(id: approval.approvalId)
                } label: {
                    Label("Approve", systemImage: "checkmark")
                        .font(.subheadline.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
            }
            .disabled(isProcessing)
        }
        .padding()
        .background(Color(.systemGray6))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(tierColor.opacity(0.3), lineWidth: 1)
        )
    }

    // MARK: - Preview content

    @ViewBuilder
    private var previewContent: some View {
        let preview = approval.preview

        if let to = preview["to"]?.value as? String {
            previewRow("To", value: to)
        }
        if let subject = preview["subject"]?.value as? String {
            previewRow("Subject", value: subject)
        }
        if let body = preview["body"]?.value as? String {
            Text(body)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(4)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.systemGray5))
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        if let title = preview["event_title"]?.value as? String {
            previewRow("Event", value: title)
        }
        if let date = preview["event_date"]?.value as? String {
            previewRow("Date", value: date)
        }
        if let url = preview["url"]?.value as? String {
            previewRow("URL", value: url)
        }
    }

    private func previewRow(_ label: String, value: String) -> some View {
        HStack(alignment: .top) {
            Text(label)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
                .frame(width: 56, alignment: .leading)
            Text(value)
                .font(.subheadline)
        }
    }

    // MARK: - Tier styling

    private var tierColor: Color {
        switch approval.actionType {
        case "email_professor", "push_production", "delete_data", "modify_infra":
            return .red
        default:
            return .orange
        }
    }

    private var iconName: String {
        switch approval.actionType {
        case "send_email", "email_professor":
            return "envelope"
        case "create_event":
            return "calendar.badge.plus"
        case "create_pr", "push_production":
            return "arrow.triangle.branch"
        case "apply_job":
            return "briefcase"
        case "delete_data":
            return "trash"
        default:
            return "exclamationmark.shield"
        }
    }
}

#Preview {
    ApprovalCard(approval: ApprovalPreview(
        approvalId: UUID(),
        actionType: "send_email",
        title: "Send email to Prof. Smith",
        preview: [
            "to": AnyCodable("smith@university.edu"),
            "subject": AnyCodable("Following up on research opportunity"),
            "body": AnyCodable("Dear Professor Smith,\n\nI wanted to follow up on our conversation about the research assistant position...")
        ]
    ))
    .environmentObject(ChatViewModel())
    .padding()
}
