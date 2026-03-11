import Foundation

// Matches backend MessageSource enum
enum MessageSource: String, Codable, Sendable {
    case ios
    case telegram
    case watch
    case siri
    case wakeWord = "wake_word"
    case system
}

// Matches backend ContentType enum
enum ContentType: String, Codable, Sendable {
    case text
    case card
    case image
    case action
    case approval
    case briefing
}

// Matches backend SendMessageRequest
struct SendMessageRequest: Codable, Sendable {
    let text: String
    let source: MessageSource
    let conversationId: UUID?
    let attachments: [Attachment]?

    enum CodingKeys: String, CodingKey {
        case text, source, attachments
        case conversationId = "conversation_id"
    }
}

struct Attachment: Codable, Sendable {
    let type: String
    let data: String
    let mimeType: String

    enum CodingKeys: String, CodingKey {
        case type, data
        case mimeType = "mime_type"
    }
}

// Matches backend TARSResponse
struct TARSResponse: Codable, Sendable {
    let conversationId: UUID
    let messageId: UUID
    let response: ResponsePayload
    let agentUsed: String
    let modelUsed: String

    enum CodingKeys: String, CodingKey {
        case response
        case conversationId = "conversation_id"
        case messageId = "message_id"
        case agentUsed = "agent_used"
        case modelUsed = "model_used"
    }
}

struct ResponsePayload: Codable, Sendable {
    let text: String
    let contentType: ContentType
    let cards: [[String: AnyCodable]]
    let approval: ApprovalPreview?

    enum CodingKeys: String, CodingKey {
        case text, cards, approval
        case contentType = "content_type"
    }
}

// Local display model — carries rich content for all message types
struct Message: Identifiable, Sendable {
    let id: UUID
    let text: String
    let source: MessageSource
    let timestamp: Date
    let contentType: ContentType
    let approval: ApprovalPreview?
    let cards: [[String: AnyCodable]]
    let agentUsed: String?
    let conversationId: UUID?

    init(
        id: UUID = UUID(),
        text: String,
        source: MessageSource,
        timestamp: Date = Date(),
        contentType: ContentType = .text,
        approval: ApprovalPreview? = nil,
        cards: [[String: AnyCodable]] = [],
        agentUsed: String? = nil,
        conversationId: UUID? = nil
    ) {
        self.id = id
        self.text = text
        self.source = source
        self.timestamp = timestamp
        self.contentType = contentType
        self.approval = approval
        self.cards = cards
        self.agentUsed = agentUsed
        self.conversationId = conversationId
    }
}
