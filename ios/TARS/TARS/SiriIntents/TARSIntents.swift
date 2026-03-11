import AppIntents
import Foundation

// MARK: - Shared helpers

/// Non-MainActor API helper for App Intents (which run off the main actor).
/// Duplicates the minimal networking logic from APIClient to avoid @MainActor constraints.
private enum IntentAPI {
    static func sendMessage(_ text: String) async throws -> String {
        let (baseURL, apiKey) = try credentials()
        let url = baseURL.appendingPathComponent("/api/v1/message")

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 30

        let body: [String: Any] = [
            "text": text,
            "source": "siri",
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateResponse(response)

        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let responseObj = json?["response"] as? [String: Any]
        guard let responseText = responseObj?["text"] as? String else {
            throw IntentError.invalidResponse
        }
        return responseText
    }

    static func getBriefing(type: String = "morning") async throws -> String {
        let (baseURL, apiKey) = try credentials()
        var components = URLComponents(url: baseURL.appendingPathComponent("/api/v1/briefing"), resolvingAgainstBaseURL: true)!
        components.queryItems = [URLQueryItem(name: "type", value: type)]

        var request = URLRequest(url: components.url!)
        request.httpMethod = "GET"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 30

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateResponse(response)

        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let narrative = json?["narrative"] as? String else {
            throw IntentError.invalidResponse
        }
        return narrative
    }

    static func getSchedule(date: Date? = nil) async throws -> String {
        let (baseURL, apiKey) = try credentials()
        var components = URLComponents(url: baseURL.appendingPathComponent("/api/v1/schedule"), resolvingAgainstBaseURL: true)!
        if let date {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            components.queryItems = [URLQueryItem(name: "date", value: formatter.string(from: date))]
        }

        var request = URLRequest(url: components.url!)
        request.httpMethod = "GET"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 30

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateResponse(response)

        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let events = json?["events"] as? [[String: Any]] else {
            throw IntentError.invalidResponse
        }

        if events.isEmpty {
            return "You have no events scheduled for today."
        }

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        let timeFormatter = DateFormatter()
        timeFormatter.dateStyle = .none
        timeFormatter.timeStyle = .short

        var lines: [String] = ["You have \(events.count) event\(events.count == 1 ? "" : "s") today."]

        for event in events {
            let title = event["title"] as? String ?? "Untitled"
            let allDay = event["all_day"] as? Bool ?? false

            if allDay {
                lines.append("\(title), all day.")
            } else if let startStr = event["start"] as? String, let startDate = iso.date(from: startStr) {
                lines.append("\(title) at \(timeFormatter.string(from: startDate)).")
            } else {
                lines.append(title + ".")
            }
        }

        if let leaveBy = json?["leave_home_by"] as? String {
            lines.append("Leave home by \(leaveBy).")
        }

        return lines.joined(separator: " ")
    }

    // MARK: - Private

    private static func credentials() throws -> (URL, String) {
        let keychain = KeychainService.shared
        guard let urlString = keychain.retrieve(forKey: .serverURL),
              let url = URL(string: urlString)
        else {
            throw IntentError.notConfigured
        }
        guard let apiKey = keychain.retrieve(forKey: .apiKey) else {
            throw IntentError.notConfigured
        }
        return (url, apiKey)
    }

    private static func validateResponse(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else {
            throw IntentError.serverError
        }
        switch http.statusCode {
        case 200...299: return
        case 401: throw IntentError.unauthorized
        default: throw IntentError.serverError
        }
    }
}

private enum IntentError: Swift.Error, CustomLocalizedStringResourceConvertible {
    case notConfigured
    case unauthorized
    case serverError
    case invalidResponse

    var localizedStringResource: LocalizedStringResource {
        switch self {
        case .notConfigured: "T.A.R.S. is not configured. Open the app and set your server URL and API key."
        case .unauthorized: "Authentication failed. Check your API key in the T.A.R.S. app."
        case .serverError: "T.A.R.S. server is not responding. Try again later."
        case .invalidResponse: "Received an unexpected response from T.A.R.S."
        }
    }
}

// MARK: - Ask T.A.R.S. Intent

struct AskTARSIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask T.A.R.S."
    static var description = IntentDescription(
        "Send a command or question to T.A.R.S. and hear the response.",
        categoryName: "Communication"
    )
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Command", requestValueDialog: "What would you like to ask T.A.R.S.?")
    var command: String

    func perform() async throws -> some IntentResult & ProvidesDialog & ReturnsValue<String> {
        let response = try await IntentAPI.sendMessage(command)
        return .result(value: response, dialog: IntentDialog(stringLiteral: response))
    }
}

// MARK: - Get Briefing Intent

struct GetBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Get T.A.R.S. Briefing"
    static var description = IntentDescription(
        "Get your morning briefing from T.A.R.S. with weather, schedule, emails, and more.",
        categoryName: "Information"
    )
    static var openAppWhenRun: Bool = false

    func perform() async throws -> some IntentResult & ProvidesDialog & ReturnsValue<String> {
        let narrative = try await IntentAPI.getBriefing()
        return .result(value: narrative, dialog: IntentDialog(stringLiteral: narrative))
    }
}

// MARK: - Get Schedule Intent

struct GetScheduleIntent: AppIntent {
    static var title: LocalizedStringResource = "Get T.A.R.S. Schedule"
    static var description = IntentDescription(
        "Get today's schedule from T.A.R.S.",
        categoryName: "Information"
    )
    static var openAppWhenRun: Bool = false

    func perform() async throws -> some IntentResult & ProvidesDialog & ReturnsValue<String> {
        let schedule = try await IntentAPI.getSchedule()
        return .result(value: schedule, dialog: IntentDialog(stringLiteral: schedule))
    }
}

// MARK: - App Shortcuts Provider

struct TARSShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskTARSIntent(),
            phrases: [
                "Ask \(.applicationName) \(\.$command)",
                "Tell \(.applicationName) \(\.$command)",
                "Ask \(.applicationName)",
            ],
            shortTitle: "Ask T.A.R.S.",
            systemImageName: "bubble.left.fill"
        )

        AppShortcut(
            intent: GetBriefingIntent(),
            phrases: [
                "\(.applicationName) briefing",
                "Get \(.applicationName) briefing",
                "Get my \(.applicationName) briefing",
                "\(.applicationName) morning briefing",
            ],
            shortTitle: "T.A.R.S. Briefing",
            systemImageName: "sun.horizon.fill"
        )

        AppShortcut(
            intent: GetScheduleIntent(),
            phrases: [
                "What's my schedule on \(.applicationName)",
                "\(.applicationName) schedule",
                "Get my \(.applicationName) schedule",
            ],
            shortTitle: "T.A.R.S. Schedule",
            systemImageName: "calendar"
        )
    }
}
