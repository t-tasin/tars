import Foundation

// MARK: - Lightweight models for Watch ↔ iPhone data transfer
// These mirror the iOS app models but are simplified for WatchConnectivity dictionary transfer.

struct WatchApproval: Identifiable, Hashable {
    let id: UUID
    let actionType: String
    let riskTier: String
    let title: String
    let previewSummary: String
    let expiresAt: Date
    let createdAt: Date

    var isExpired: Bool { expiresAt < Date() }

    var riskColor: String {
        switch riskTier {
        case "tier3_escalation": return "red"
        case "tier2_approval": return "orange"
        default: return "green"
        }
    }

    var actionIcon: String {
        switch actionType {
        case "send_email", "email_professor": return "envelope"
        case "create_event": return "calendar.badge.plus"
        case "create_pr": return "arrow.triangle.pull"
        case "apply_job": return "briefcase"
        case "archive_emails": return "archivebox"
        case "create_notion": return "doc.badge.plus"
        case "push_production": return "server.rack"
        case "delete_data": return "trash"
        default: return "checkmark.circle"
        }
    }

    // MARK: - Dictionary serialization for WatchConnectivity

    func toDictionary() -> [String: Any] {
        [
            "id": id.uuidString,
            "actionType": actionType,
            "riskTier": riskTier,
            "title": title,
            "previewSummary": previewSummary,
            "expiresAt": expiresAt.timeIntervalSince1970,
            "createdAt": createdAt.timeIntervalSince1970,
        ]
    }

    static func fromDictionary(_ dict: [String: Any]) -> WatchApproval? {
        guard let idString = dict["id"] as? String,
              let id = UUID(uuidString: idString),
              let actionType = dict["actionType"] as? String,
              let riskTier = dict["riskTier"] as? String,
              let title = dict["title"] as? String,
              let previewSummary = dict["previewSummary"] as? String,
              let expiresAtInterval = dict["expiresAt"] as? TimeInterval,
              let createdAtInterval = dict["createdAt"] as? TimeInterval
        else { return nil }

        return WatchApproval(
            id: id,
            actionType: actionType,
            riskTier: riskTier,
            title: title,
            previewSummary: previewSummary,
            expiresAt: Date(timeIntervalSince1970: expiresAtInterval),
            createdAt: Date(timeIntervalSince1970: createdAtInterval)
        )
    }
}

struct WatchEvent: Identifiable, Hashable {
    let id: String
    let title: String
    let start: Date
    let end: Date
    let location: String?
    let calendar: String
    let allDay: Bool

    var timeRange: String {
        if allDay { return "All Day" }
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return "\(formatter.string(from: start)) – \(formatter.string(from: end))"
    }

    var isNow: Bool {
        let now = Date()
        return start <= now && end > now
    }

    var minutesUntilStart: Int {
        Int(start.timeIntervalSinceNow / 60)
    }

    func toDictionary() -> [String: Any] {
        var dict: [String: Any] = [
            "id": id,
            "title": title,
            "start": start.timeIntervalSince1970,
            "end": end.timeIntervalSince1970,
            "calendar": calendar,
            "allDay": allDay,
        ]
        if let location { dict["location"] = location }
        return dict
    }

    static func fromDictionary(_ dict: [String: Any]) -> WatchEvent? {
        guard let id = dict["id"] as? String,
              let title = dict["title"] as? String,
              let startInterval = dict["start"] as? TimeInterval,
              let endInterval = dict["end"] as? TimeInterval,
              let calendar = dict["calendar"] as? String,
              let allDay = dict["allDay"] as? Bool
        else { return nil }

        return WatchEvent(
            id: id,
            title: title,
            start: Date(timeIntervalSince1970: startInterval),
            end: Date(timeIntervalSince1970: endInterval),
            location: dict["location"] as? String,
            calendar: calendar,
            allDay: allDay
        )
    }
}

// MARK: - WatchConnectivity message keys

enum WatchMessageKey {
    static let type = "type"
    static let events = "events"
    static let approvals = "approvals"
    static let approvalId = "approvalId"
    static let decision = "decision"
    static let pendingCount = "pendingCount"
    static let leaveHomeBy = "leaveHomeBy"
    static let commuteNote = "commuteNote"
    static let lastSyncDate = "lastSyncDate"

    // Message types
    static let syncData = "syncData"
    static let approvalDecision = "approvalDecision"
    static let requestSync = "requestSync"
    static let approvalUpdate = "approvalUpdate"
}
