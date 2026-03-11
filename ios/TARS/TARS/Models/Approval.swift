import Foundation

// Matches backend ApprovalStatus enum
enum ApprovalStatus: String, Codable, Sendable {
    case pending
    case approved
    case rejected
    case edited
    case expired
    case executed
}

// Matches backend RiskTier enum
enum RiskTier: String, Codable, Sendable {
    case tier1Autonomous = "tier1_autonomous"
    case tier2Approval = "tier2_approval"
    case tier3Escalation = "tier3_escalation"
}

// Matches backend ApprovalPreview
struct ApprovalPreview: Codable, Sendable {
    let approvalId: UUID
    let actionType: String
    let title: String
    let preview: [String: AnyCodable]

    enum CodingKeys: String, CodingKey {
        case title, preview
        case approvalId = "approval_id"
        case actionType = "action_type"
    }
}

// Matches backend ApprovalDetail
struct Approval: Codable, Identifiable, Sendable {
    let id: UUID
    let actionType: String
    let riskTier: RiskTier
    let status: ApprovalStatus
    let title: String
    let preview: [String: AnyCodable]
    let expiresAt: Date
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, status, title, preview
        case actionType = "action_type"
        case riskTier = "risk_tier"
        case expiresAt = "expires_at"
        case createdAt = "created_at"
    }
}

// Matches backend ApprovalDecisionRequest
struct ApprovalDecisionRequest: Codable, Sendable {
    let decision: String // "approved", "rejected", "edited"
    let source: MessageSource
    let reason: String?
    let editedPayload: [String: AnyCodable]?

    enum CodingKeys: String, CodingKey {
        case decision, source, reason
        case editedPayload = "edited_payload"
    }
}

// Matches backend ApprovalsListResponse
struct ApprovalsListResponse: Codable, Sendable {
    let approvals: [Approval]
    let total: Int
    let pendingCount: Int

    enum CodingKeys: String, CodingKey {
        case approvals, total
        case pendingCount = "pending_count"
    }
}

// Matches backend ApprovalActionResponse
struct ApprovalActionResponse: Codable, Sendable {
    let approvalId: UUID
    let status: ApprovalStatus
    let executed: Bool
    let executionResult: String?

    enum CodingKeys: String, CodingKey {
        case status, executed
        case approvalId = "approval_id"
        case executionResult = "execution_result"
    }
}
