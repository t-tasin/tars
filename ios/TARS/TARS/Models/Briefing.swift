import Foundation

// Matches backend BriefingResponse
struct Briefing: Codable, Identifiable, Sendable {
    let briefingId: UUID
    let type: String
    let date: String
    let payload: [String: AnyCodable]
    let narrative: String
    let createdAt: Date
    let delivered: Bool

    var id: UUID { briefingId }

    enum CodingKeys: String, CodingKey {
        case type, date, payload, narrative, delivered
        case briefingId = "briefing_id"
        case createdAt = "created_at"
    }
}

// Matches backend ScheduleResponse
struct ScheduleResponse: Codable, Sendable {
    let date: String
    let events: [CalendarEvent]
    let leaveHomeBy: String?
    let commuteNote: String?

    enum CodingKeys: String, CodingKey {
        case date, events
        case leaveHomeBy = "leave_home_by"
        case commuteNote = "commute_note"
    }
}

struct CalendarEvent: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let start: Date
    let end: Date
    let location: String?
    let calendar: String
    let allDay: Bool

    enum CodingKeys: String, CodingKey {
        case id, title, start, end, location, calendar
        case allDay = "all_day"
    }
}
