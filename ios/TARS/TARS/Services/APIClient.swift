import Foundation

// MARK: - Missing response models (not yet in Models/)

struct OutfitResponse: Codable, Sendable {
    let outfitId: UUID
    let date: String
    let suggestion: String
    let reasoning: String
    let items: [[String: AnyCodable]]
    let weather: [String: AnyCodable]

    enum CodingKeys: String, CodingKey {
        case date, suggestion, reasoning, items, weather
        case outfitId = "outfit_id"
    }
}

// MARK: - Endpoint

enum Endpoint {
    case sendMessage(SendMessageRequest)
    case briefing(type: String)
    case schedule(date: Date?)
    case approvals(status: String)
    case approve(id: String)
    case reject(id: String, reason: String?)
    case jobs(status: String)
    case outfit
    case registerDeviceToken(RegisterDeviceTokenRequest)
    case custom(path: String, method: String, body: (any Encodable)? = nil)

    var path: String {
        switch self {
        case .sendMessage:           return "/api/v1/message"
        case .briefing:              return "/api/v1/briefing"
        case .schedule:              return "/api/v1/schedule"
        case .approvals:             return "/api/v1/approvals"
        case .approve(let id):       return "/api/v1/approvals/\(id)/approve"
        case .reject(let id, _):     return "/api/v1/approvals/\(id)/reject"
        case .jobs:                  return "/api/v1/jobs"
        case .outfit:                return "/api/v1/outfit"
        case .registerDeviceToken:   return "/api/v1/device-tokens"
        case .custom(let path, _, _): return path
        }
    }

    var method: String {
        switch self {
        case .sendMessage, .approve, .reject, .registerDeviceToken:
            return "POST"
        case .custom(_, let method, _):
            return method
        default:
            return "GET"
        }
    }

    var queryItems: [URLQueryItem] {
        switch self {
        case .briefing(let type):
            return [URLQueryItem(name: "type", value: type)]
        case .schedule(let date):
            guard let date else { return [] }
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            return [URLQueryItem(name: "date", value: formatter.string(from: date))]
        case .approvals(let status):
            return [URLQueryItem(name: "status", value: status)]
        case .jobs(let status):
            return [URLQueryItem(name: "status", value: status)]
        default:
            return []
        }
    }

    var body: (any Encodable)? {
        switch self {
        case .sendMessage(let request):
            return request
        case .approve:
            return ApproveRequestBody(source: .ios)
        case .reject(_, let reason):
            return RejectRequestBody(source: .ios, reason: reason)
        case .registerDeviceToken(let request):
            return request
        case .custom(_, _, let body):
            return body
        default:
            return nil
        }
    }
}

// Request bodies for approve/reject (match backend ApproveRequest / RejectRequest)
private struct ApproveRequestBody: Encodable {
    let source: MessageSource
}

private struct RejectRequestBody: Encodable {
    let source: MessageSource
    let reason: String?
}

// MARK: - Device token registration

struct RegisterDeviceTokenRequest: Encodable {
    let token: String
    let deviceName: String?
    let platform: String
}

struct RegisterDeviceTokenResponse: Decodable {
    let registered: Bool
    let message: String
}

// MARK: - Errors

enum APIError: LocalizedError {
    case notConfigured
    case invalidURL
    case unauthorized
    case notFound(String)
    case conflict(String)
    case expired(String)
    case serverError(String)
    case decodingFailed(Error)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .notConfigured:          return "Server URL or API key not configured"
        case .invalidURL:             return "Invalid request URL"
        case .unauthorized:           return "Invalid credentials"
        case .notFound(let msg):      return msg
        case .conflict(let msg):      return msg
        case .expired(let msg):       return msg
        case .serverError(let msg):   return msg
        case .decodingFailed(let e):  return "Failed to decode response: \(e.localizedDescription)"
        case .networkError(let e):    return "Network error: \(e.localizedDescription)"
        }
    }
}

// MARK: - APIClient

@MainActor
final class APIClient: ObservableObject {
    static let shared = APIClient()

    private let session: URLSession
    private let keychain = KeychainService.shared
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 60
        self.session = URLSession(configuration: config)

        self.encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase

        self.decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)

            // Try ISO 8601 with fractional seconds first
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = iso.date(from: string) { return date }

            // Fall back to without fractional seconds
            iso.formatOptions = [.withInternetDateTime]
            if let date = iso.date(from: string) { return date }

            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Cannot decode date: \(string)"
            )
        }
    }

    // MARK: - Configuration

    var isConfigured: Bool {
        keychain.retrieve(forKey: .serverURL) != nil && keychain.retrieve(forKey: .apiKey) != nil
    }

    private var baseURL: URL {
        get throws {
            guard let urlString = keychain.retrieve(forKey: .serverURL),
                  let url = URL(string: urlString)
            else { throw APIError.notConfigured }
            return url
        }
    }

    private var apiKey: String {
        get throws {
            guard let key = keychain.retrieve(forKey: .apiKey)
            else { throw APIError.notConfigured }
            return key
        }
    }

    private var deviceToken: String? {
        keychain.retrieve(forKey: .deviceToken)
    }

    // MARK: - Generic request

    func request<T: Decodable>(_ endpoint: Endpoint) async throws -> T {
        let base = try baseURL
        guard var components = URLComponents(url: base.appendingPathComponent(endpoint.path), resolvingAgainstBaseURL: true)
        else { throw APIError.invalidURL }

        let queryItems = endpoint.queryItems
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }

        guard let url = components.url else { throw APIError.invalidURL }

        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = endpoint.method
        urlRequest.setValue("Bearer \(try apiKey)", forHTTPHeaderField: "Authorization")
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")

        if let token = deviceToken {
            urlRequest.setValue(token, forHTTPHeaderField: "X-Device-Token")
        }

        if let body = endpoint.body {
            urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
            urlRequest.httpBody = try encoder.encode(AnyEncodable(body))
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIError.networkError(error)
        }

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.serverError("Invalid response")
        }

        switch httpResponse.statusCode {
        case 200...299:
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw APIError.decodingFailed(error)
            }

        case 401:
            throw APIError.unauthorized

        case 404:
            let msg = extractErrorMessage(from: data) ?? "Not found"
            throw APIError.notFound(msg)

        case 409:
            let msg = extractErrorMessage(from: data) ?? "Conflict"
            throw APIError.conflict(msg)

        case 410:
            let msg = extractErrorMessage(from: data) ?? "Expired"
            throw APIError.expired(msg)

        default:
            let msg = extractErrorMessage(from: data) ?? "Server error (\(httpResponse.statusCode))"
            throw APIError.serverError(msg)
        }
    }

    private func extractErrorMessage(from data: Data) -> String? {
        try? decoder.decode(TARSErrorResponse.self, from: data).error.message
    }

    // MARK: - Convenience methods

    func sendMessage(text: String, source: MessageSource = .ios, conversationId: UUID? = nil) async throws -> TARSResponse {
        let body = SendMessageRequest(text: text, source: source, conversationId: conversationId, attachments: nil)
        return try await request(.sendMessage(body))
    }

    func getBriefing(type: String = "morning") async throws -> Briefing {
        try await request(.briefing(type: type))
    }

    func getSchedule(date: Date? = nil) async throws -> ScheduleResponse {
        try await request(.schedule(date: date))
    }

    func getApprovals(status: String = "pending") async throws -> ApprovalsListResponse {
        try await request(.approvals(status: status))
    }

    func approveAction(id: String) async throws -> ApprovalActionResponse {
        try await request(.approve(id: id))
    }

    func rejectAction(id: String, reason: String? = nil) async throws -> ApprovalActionResponse {
        try await request(.reject(id: id, reason: reason))
    }

    func getJobs(status: String = "new") async throws -> JobsListResponse {
        try await request(.jobs(status: status))
    }

    func getOutfit() async throws -> OutfitResponse {
        try await request(.outfit)
    }

    func registerDeviceToken(token: String, deviceName: String?, platform: String = "ios") async throws -> RegisterDeviceTokenResponse {
        let body = RegisterDeviceTokenRequest(token: token, deviceName: deviceName, platform: platform)
        return try await request(.registerDeviceToken(body))
    }
}

// MARK: - Workout endpoints

extension Endpoint {
    static func createSplit(_ body: CreateSplitRequest) -> Endpoint {
        .custom(path: "/api/v1/workout/splits", method: "POST", body: body)
    }

    static var activeSplit: Endpoint {
        .custom(path: "/api/v1/workout/splits/active", method: "GET")
    }

    static func todaySession() -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/today", method: "GET")
    }

    static func startSession(_ sessionId: String) -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/\(sessionId)/start", method: "POST")
    }

    static func skipSession(_ sessionId: String, reason: String) -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/\(sessionId)/skip", method: "POST", body: SkipSessionBody(reason: reason))
    }

    static func completeSession(_ sessionId: String) -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/\(sessionId)/complete", method: "POST")
    }

    static func logSet(_ body: LogSetBody) -> Endpoint {
        .custom(path: "/api/v1/workout/logs", method: "POST", body: body)
    }

    static var workoutStreak: Endpoint {
        .custom(path: "/api/v1/workout/streak", method: "GET")
    }
}

// MARK: - Workout models

struct CreateSplitRequest: Codable, Sendable {
    let name: String
    let rotationDays: [String]
    let exercises: [CreateExerciseBody]
}

struct CreateExerciseBody: Codable, Sendable {
    let dayName: String
    let exerciseName: String
    let targetSets: Int
    let targetReps: Int
    let currentWeight: Double
    let weightUnit: String
    let weightIncrement: Double
}

struct SkipSessionBody: Codable, Sendable {
    let reason: String
}

struct LogSetBody: Codable, Sendable {
    let sessionId: String
    let exerciseId: String
    let setNumber: Int
    let actualReps: Int
    let actualWeight: Double
}

struct WorkoutSessionResponse: Codable, Sendable {
    let id: String
    let dayName: String
    let status: String
    let logs: [WorkoutLogResponse]
}

struct WorkoutLogResponse: Codable, Sendable, Identifiable {
    let id: String
    let exerciseId: String
    let setNumber: Int
    let targetReps: Int
    let targetWeight: Double
    let actualReps: Int?
    let actualWeight: Double?
}

struct StreakResponseModel: Codable, Sendable {
    let streak: Int
    let recentSkips: [[String: String]]
}

// MARK: - Type-erased Encodable wrapper

private struct AnyEncodable: Encodable {
    private let _encode: (Encoder) throws -> Void

    init(_ wrapped: any Encodable) {
        _encode = wrapped.encode
    }

    func encode(to encoder: Encoder) throws {
        try _encode(encoder)
    }
}
