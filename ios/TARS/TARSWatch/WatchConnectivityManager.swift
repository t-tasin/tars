import Foundation
import WatchConnectivity
import WidgetKit

/// Manages WatchConnectivity session on the watchOS side.
/// Receives schedule + approval data from iPhone, sends approval decisions back.
@MainActor
final class WatchConnectivityManager: NSObject, ObservableObject {
    static let shared = WatchConnectivityManager()

    @Published var events: [WatchEvent] = []
    @Published var approvals: [WatchApproval] = []
    @Published var pendingCount: Int = 0
    @Published var leaveHomeBy: String?
    @Published var commuteNote: String?
    @Published var lastSyncDate: Date?
    @Published var isReachable: Bool = false

    private var session: WCSession?

    var nextEvent: WatchEvent? {
        let now = Date()
        return events
            .filter { !$0.allDay && $0.end > now }
            .sorted { $0.start < $1.start }
            .first
    }

    override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        session = WCSession.default
        session?.delegate = self
        session?.activate()
    }

    // MARK: - Send approval decision to iPhone

    func sendApprovalDecision(approvalId: UUID, decision: String) {
        let message: [String: Any] = [
            WatchMessageKey.type: WatchMessageKey.approvalDecision,
            WatchMessageKey.approvalId: approvalId.uuidString,
            WatchMessageKey.decision: decision,
        ]

        session?.sendMessage(message, replyHandler: { [weak self] reply in
            Task { @MainActor in
                guard let self else { return }
                // Remove from local list on success
                if reply["success"] as? Bool == true {
                    self.approvals.removeAll { $0.id == approvalId }
                    self.pendingCount = max(0, self.pendingCount - 1)
                }
            }
        }, errorHandler: { error in
            // iPhone unreachable — will sync on next connection
            print("[WatchConnectivity] Send failed: \(error.localizedDescription)")
        })
    }

    // MARK: - Request fresh data from iPhone

    func requestSync() {
        let message: [String: Any] = [
            WatchMessageKey.type: WatchMessageKey.requestSync,
        ]

        session?.sendMessage(message, replyHandler: { [weak self] reply in
            Task { @MainActor in
                self?.processReceivedData(reply)
            }
        }, errorHandler: { error in
            print("[WatchConnectivity] Sync request failed: \(error.localizedDescription)")
        })
    }

    // MARK: - Process data from iPhone

    private func processReceivedData(_ data: [String: Any]) {
        if let eventDicts = data[WatchMessageKey.events] as? [[String: Any]] {
            events = eventDicts.compactMap(WatchEvent.fromDictionary)
        }

        if let approvalDicts = data[WatchMessageKey.approvals] as? [[String: Any]] {
            approvals = approvalDicts.compactMap(WatchApproval.fromDictionary)
        }

        if let count = data[WatchMessageKey.pendingCount] as? Int {
            pendingCount = count
        }

        leaveHomeBy = data[WatchMessageKey.leaveHomeBy] as? String
        commuteNote = data[WatchMessageKey.commuteNote] as? String

        if let syncInterval = data[WatchMessageKey.lastSyncDate] as? TimeInterval {
            lastSyncDate = Date(timeIntervalSince1970: syncInterval)
        } else {
            lastSyncDate = Date()
        }

        updateComplicationData()
    }

    // MARK: - Complication data sync

    private func updateComplicationData() {
        guard let defaults = UserDefaults(suiteName: "group.com.tasin.tars") else { return }

        if let next = nextEvent {
            defaults.set(next.title, forKey: "complication_nextEventTitle")
            defaults.set(next.start.timeIntervalSince1970, forKey: "complication_nextEventTime")
        } else {
            defaults.removeObject(forKey: "complication_nextEventTitle")
            defaults.set(0, forKey: "complication_nextEventTime")
        }

        defaults.set(pendingCount, forKey: "complication_pendingApprovals")
        defaults.set(leaveHomeBy, forKey: "complication_leaveHomeBy")

        WidgetCenter.shared.reloadAllTimelines()
    }
}

// MARK: - WCSessionDelegate

extension WatchConnectivityManager: WCSessionDelegate {
    nonisolated func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        Task { @MainActor in
            self.isReachable = session.isReachable
            if activationState == .activated {
                self.requestSync()
            }
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in
            self.isReachable = session.isReachable
            if session.isReachable {
                self.requestSync()
            }
        }
    }

    // iPhone pushed new data
    nonisolated func session(
        _ session: WCSession,
        didReceiveMessage message: [String: Any]
    ) {
        Task { @MainActor in
            self.processReceivedData(message)
        }
    }

    nonisolated func session(
        _ session: WCSession,
        didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void
    ) {
        Task { @MainActor in
            self.processReceivedData(message)
            replyHandler(["received": true])
        }
    }

    // Background transfer via applicationContext
    nonisolated func session(
        _ session: WCSession,
        didReceiveApplicationContext applicationContext: [String: Any]
    ) {
        Task { @MainActor in
            self.processReceivedData(applicationContext)
        }
    }

    // UserInfo transfer (queued, guaranteed delivery)
    nonisolated func session(
        _ session: WCSession,
        didReceiveUserInfo userInfo: [String: Any]
    ) {
        Task { @MainActor in
            let msgType = userInfo[WatchMessageKey.type] as? String
            if msgType == WatchMessageKey.approvalUpdate {
                self.processReceivedData(userInfo)
            }
        }
    }
}
