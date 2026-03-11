import Foundation

// Matches backend JobStatus enum
enum JobStatus: String, Codable, Sendable {
    case new
    case saved
    case applying
    case applied
    case interview
    case offer
    case rejected
    case skipped
    case expired
}

// Matches backend JobDetail
struct JobListing: Codable, Identifiable, Sendable {
    let id: UUID
    let title: String
    let company: String
    let location: String?
    let salaryRange: String?
    let matchScore: Int?
    let matchReasons: [String]
    let concerns: [String]
    let url: String
    let status: JobStatus
    let source: String
    let scrapedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title, company, location, url, status, source, concerns
        case salaryRange = "salary_range"
        case matchScore = "match_score"
        case matchReasons = "match_reasons"
        case scrapedAt = "scraped_at"
    }
}

// Matches backend JobsListResponse
struct JobsListResponse: Codable, Sendable {
    let jobs: [JobListing]
    let total: Int
    let newToday: Int

    enum CodingKeys: String, CodingKey {
        case jobs, total
        case newToday = "new_today"
    }
}

// Matches backend JobActionRequest
struct JobActionRequest: Codable, Sendable {
    let action: String // "save", "skip", "apply", "archive"
}
