import Foundation

// Matches backend ErrorResponse
struct TARSErrorResponse: Codable, Sendable {
    let error: ErrorDetail
}

struct ErrorDetail: Codable, Sendable {
    let code: String
    let message: String
    let details: [String: AnyCodable]
}
