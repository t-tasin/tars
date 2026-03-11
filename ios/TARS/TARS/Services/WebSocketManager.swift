import Foundation

// MARK: - WebSocket message envelope (matches backend _build_payload)

struct WSMessage: Sendable {
    let type: WSMessageType
    let data: [String: AnyCodable]
    let timestamp: Date
}

enum WSMessageType: String, Codable, Sendable {
    case message
    case approvalRequest = "approval_request"
    case agentStatus = "agent_status"
    case notification
    case briefingReady = "briefing_ready"
    case deployStatus = "deploy_status"
    case pong
}

// Raw envelope for decoding
private struct WSEnvelope: Decodable {
    let type: WSMessageType
    let data: [String: AnyCodable]
    let timestamp: String
}

// MARK: - WebSocketManager

@MainActor
final class WebSocketManager: ObservableObject {
    static let shared = WebSocketManager()

    @Published var isConnected = false
    @Published var lastMessage: WSMessage?

    private var task: URLSessionWebSocketTask?
    private var pingTimer: Timer?
    private var handlers: [(WSMessage) -> Void] = []
    private var reconnectAttempts = 0
    private let maxReconnectAttempts = 10
    private let keychain = KeychainService.shared

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private init() {}

    // MARK: - Connect / Disconnect

    func connect() {
        guard task == nil else { return }

        guard let urlString = keychain.retrieve(forKey: .serverURL),
              let apiKey = keychain.retrieve(forKey: .apiKey)
        else { return }

        // Build ws:// or wss:// URL with auth query params
        var wsURLString = urlString
            .replacingOccurrences(of: "https://", with: "wss://")
            .replacingOccurrences(of: "http://", with: "ws://")

        wsURLString += "/api/v1/stream"

        var components = URLComponents(string: wsURLString)
        var queryItems = [URLQueryItem(name: "token", value: apiKey)]
        if let device = keychain.retrieve(forKey: .deviceToken) {
            queryItems.append(URLQueryItem(name: "device", value: device))
        }
        components?.queryItems = queryItems

        guard let url = components?.url else { return }

        let session = URLSession(configuration: .default)
        task = session.webSocketTask(with: url)
        task?.resume()

        isConnected = true
        reconnectAttempts = 0
        startListening()
        startPingTimer()
    }

    func disconnect() {
        pingTimer?.invalidate()
        pingTimer = nil
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        isConnected = false
    }

    // MARK: - Message handler registration

    func onMessage(_ handler: @escaping @Sendable (WSMessage) -> Void) {
        handlers.append(handler)
    }

    // MARK: - Listening

    private func startListening() {
        task?.receive { [weak self] result in
            Task { @MainActor in
                guard let self else { return }

                switch result {
                case .success(let message):
                    self.handleRawMessage(message)
                    self.startListening() // Continue listening

                case .failure:
                    self.handleDisconnect()
                }
            }
        }
    }

    private func handleRawMessage(_ message: URLSessionWebSocketTask.Message) {
        let data: Data
        switch message {
        case .string(let text):
            guard let textData = text.data(using: .utf8) else { return }
            data = textData
        case .data(let binaryData):
            data = binaryData
        @unknown default:
            return
        }

        guard let envelope = try? decoder.decode(WSEnvelope.self, from: data) else { return }

        // Parse timestamp
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let timestamp = iso.date(from: envelope.timestamp) ?? Date()

        let wsMessage = WSMessage(
            type: envelope.type,
            data: envelope.data,
            timestamp: timestamp
        )

        lastMessage = wsMessage

        for handler in handlers {
            handler(wsMessage)
        }
    }

    // MARK: - Ping / keep-alive

    private func startPingTimer() {
        pingTimer?.invalidate()
        pingTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.sendPing()
            }
        }
    }

    private func sendPing() {
        guard let task, task.state == .running else { return }

        let ping = try? JSONEncoder().encode(["type": "ping"])
        guard let ping else { return }

        task.send(.data(ping)) { [weak self] error in
            if error != nil {
                Task { @MainActor in
                    self?.handleDisconnect()
                }
            }
        }
    }

    // MARK: - Reconnection

    private func handleDisconnect() {
        task = nil
        isConnected = false
        pingTimer?.invalidate()
        pingTimer = nil

        guard reconnectAttempts < maxReconnectAttempts else { return }

        reconnectAttempts += 1
        // Exponential backoff: 1s, 2s, 4s, 8s, ... capped at 60s
        let delay = min(pow(2.0, Double(reconnectAttempts - 1)), 60.0)

        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            self?.connect()
        }
    }
}
