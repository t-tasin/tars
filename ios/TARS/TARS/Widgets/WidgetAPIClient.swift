import Foundation
import Security

/// Lightweight API client for widget extensions.
/// Widgets run in a separate process and cannot use the main app's @MainActor APIClient.
/// Reads credentials from the shared Keychain (same service name as the main app).
struct WidgetAPIClient {
    private static let serviceName = "com.tasin.tars"

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)

            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = iso.date(from: string) { return date }

            iso.formatOptions = [.withInternetDateTime]
            if let date = iso.date(from: string) { return date }

            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Cannot decode date: \(string)"
            )
        }
        return d
    }()

    // MARK: - Keychain

    private static func keychainRetrieve(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: serviceName,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8)
        else { return nil }
        return value
    }

    private static var serverURL: String? { keychainRetrieve("tars_server_url") }
    private static var apiKey: String? { keychainRetrieve("tars_api_key") }

    static var isConfigured: Bool { serverURL != nil && apiKey != nil }

    // MARK: - Fetch

    static func fetch<T: Decodable>(path: String, queryItems: [URLQueryItem] = []) async -> T? {
        guard let urlString = serverURL,
              let baseURL = URL(string: urlString),
              let key = apiKey
        else { return nil }

        guard var components = URLComponents(
            url: baseURL.appendingPathComponent(path),
            resolvingAgainstBaseURL: true
        ) else { return nil }

        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }

        guard let url = components.url else { return nil }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                return nil
            }
            return try decoder.decode(T.self, from: data)
        } catch {
            return nil
        }
    }

    // MARK: - Widget-specific fetches

    static func fetchSchedule(date: Date = .now) async -> WidgetScheduleResponse? {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        return await fetch(
            path: "/api/v1/schedule",
            queryItems: [URLQueryItem(name: "date", value: formatter.string(from: date))]
        )
    }

    static func fetchPendingApprovals() async -> WidgetApprovalsResponse? {
        await fetch(
            path: "/api/v1/approvals",
            queryItems: [URLQueryItem(name: "status", value: "pending")]
        )
    }

    static func fetchHealthSummary() async -> WidgetHealthResponse? {
        await fetch(path: "/api/v1/health")
    }

    static func fetchOutfit() async -> WidgetOutfitResponse? {
        await fetch(path: "/api/v1/outfit")
    }
}

// MARK: - Widget-specific response models
// Lightweight Codable structs that mirror backend responses.

struct WidgetScheduleResponse: Codable {
    let date: String
    let events: [WidgetCalendarEvent]
    let leaveHomeBy: String?
}

struct WidgetCalendarEvent: Codable, Identifiable {
    let id: String
    let title: String
    let start: Date
    let end: Date
    let location: String?
    let calendar: String
    let allDay: Bool
}

struct WidgetApprovalsResponse: Codable {
    let approvals: [WidgetApproval]
    let total: Int
    let pendingCount: Int
}

struct WidgetApproval: Codable, Identifiable {
    let id: UUID
    let actionType: String
    let title: String
    let expiresAt: Date
}

struct WidgetHealthResponse: Codable {
    let sleepHours: Double?
    let steps: Int?
    let heartRate: Int?
    let exerciseMinutes: Int?
    let lastUpdated: String?
}

struct WidgetOutfitResponse: Codable {
    let outfitId: UUID?
    let date: String
    let suggestion: String
    let reasoning: String
    let weather: WidgetWeather?
}

struct WidgetWeather: Codable {
    let tempF: Int?
    let condition: String?
    let high: Int?
    let low: Int?
}
