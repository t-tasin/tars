import Foundation
import Combine

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [Message] = []
    @Published var inputText = ""
    @Published var isSending = false
    @Published var error: String?

    private var conversationId: UUID?
    private let api = APIClient.shared
    private let ws = WebSocketManager.shared

    init() {
        setupWebSocket()
    }

    // MARK: - Send

    func send() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }

        let userMessage = Message(
            text: text,
            source: .ios,
            conversationId: conversationId
        )
        messages.append(userMessage)
        inputText = ""
        isSending = true
        error = nil

        Task {
            do {
                let response = try await api.sendMessage(
                    text: text,
                    source: .ios,
                    conversationId: conversationId
                )
                conversationId = response.conversationId
                appendTARSResponse(response)
            } catch {
                self.error = error.localizedDescription
            }
            isSending = false
        }
    }

    func clearConversation() {
        messages.removeAll()
        conversationId = nil
        error = nil
    }

    // MARK: - Approvals

    func approve(id: UUID) {
        Task {
            do {
                _ = try await api.approveAction(id: id.uuidString)
                markApprovalHandled(id: id, status: "approved")
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    func reject(id: UUID, reason: String? = nil) {
        Task {
            do {
                _ = try await api.rejectAction(id: id.uuidString, reason: reason)
                markApprovalHandled(id: id, status: "rejected")
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    // MARK: - WebSocket

    private func setupWebSocket() {
        ws.onMessage { [weak self] wsMessage in
            Task { @MainActor in
                self?.handleWSMessage(wsMessage)
            }
        }
        ws.connect()
    }

    private func handleWSMessage(_ wsMessage: WSMessage) {
        switch wsMessage.type {
        case .message:
            guard let text = wsMessage.data["text"]?.value as? String else { return }
            let contentTypeRaw = (wsMessage.data["content_type"]?.value as? String) ?? "text"
            let contentType = ContentType(rawValue: contentTypeRaw) ?? .text

            let message = Message(
                text: text,
                source: .system,
                timestamp: wsMessage.timestamp,
                contentType: contentType
            )
            messages.append(message)

        case .approvalRequest:
            guard let title = wsMessage.data["title"]?.value as? String,
                  let idStr = wsMessage.data["approval_id"]?.value as? String,
                  let approvalId = UUID(uuidString: idStr),
                  let actionType = wsMessage.data["action_type"]?.value as? String
            else { return }

            let preview = ApprovalPreview(
                approvalId: approvalId,
                actionType: actionType,
                title: title,
                preview: wsMessage.data
            )
            let message = Message(
                text: title,
                source: .system,
                timestamp: wsMessage.timestamp,
                contentType: .approval,
                approval: preview
            )
            messages.append(message)

        case .notification:
            guard let text = wsMessage.data["text"]?.value as? String else { return }
            let message = Message(
                text: text,
                source: .system,
                timestamp: wsMessage.timestamp
            )
            messages.append(message)

        default:
            break
        }
    }

    // MARK: - Helpers

    private func appendTARSResponse(_ response: TARSResponse) {
        let message = Message(
            id: response.messageId,
            text: response.response.text,
            source: .system,
            contentType: response.response.contentType,
            approval: response.response.approval,
            cards: response.response.cards,
            agentUsed: response.agentUsed,
            conversationId: response.conversationId
        )
        messages.append(message)
    }

    private func markApprovalHandled(id: UUID, status: String) {
        guard let idx = messages.firstIndex(where: { $0.approval?.approvalId == id }) else { return }
        let old = messages[idx]
        messages[idx] = Message(
            id: old.id,
            text: "\(old.text) — \(status)",
            source: old.source,
            timestamp: old.timestamp,
            contentType: .text,
            approval: nil,
            cards: old.cards,
            agentUsed: old.agentUsed,
            conversationId: old.conversationId
        )
    }
}
