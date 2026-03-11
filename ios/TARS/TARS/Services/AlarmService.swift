import Foundation
import UserNotifications
import AVFoundation
import MediaPlayer

// MARK: - Alarm config from backend

struct AlarmConfig: Codable, Sendable {
    let enabled: Bool
    let wakeTime: String?
    let commuteMinutes: Int?
    let prepMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case enabled
        case wakeTime = "wake_time"
        case commuteMinutes = "commute_minutes"
        case prepMinutes = "prep_minutes"
    }
}

struct ConfigResponse: Codable, Sendable {
    let alarm: AlarmConfig?
}

// MARK: - AlarmService

@MainActor
final class AlarmService: ObservableObject {
    static let shared = AlarmService()

    @Published var scheduledAlarmTime: Date?
    @Published var isAlarmActive = false
    @Published var isSnoozed = false
    @Published var error: String?

    private let api = APIClient.shared
    private let eventKit = EventKitService.shared
    private let speech = SpeechService.shared

    static let alarmCategoryIdentifier = "TARS_ALARM"
    static let dismissActionIdentifier = "ALARM_DISMISS"
    static let snoozeActionIdentifier = "ALARM_SNOOZE"
    static let notificationIdentifier = "tars-smart-alarm"
    static let snoozeDurationMinutes = 5

    private init() {}

    // MARK: - Notification category registration

    func registerAlarmCategory() {
        let dismiss = UNNotificationAction(
            identifier: Self.dismissActionIdentifier,
            title: "Dismiss",
            options: [.foreground]
        )
        let snooze = UNNotificationAction(
            identifier: Self.snoozeActionIdentifier,
            title: "Snooze (5 min)",
            options: []
        )
        let category = UNNotificationCategory(
            identifier: Self.alarmCategoryIdentifier,
            actions: [dismiss, snooze],
            intentIdentifiers: [],
            options: [.customDismissAction]
        )

        let center = UNUserNotificationCenter.current()
        center.getNotificationCategories { existing in
            var categories = existing
            categories.insert(category)
            center.setNotificationCategories(categories)
        }
    }

    // MARK: - Fetch alarm config from backend

    func syncAlarmFromBackend() async {
        do {
            let config: ConfigResponse = try await api.request(
                .custom(path: "/api/v1/config", method: "GET", body: nil)
            )
            guard let alarm = config.alarm, alarm.enabled, let wakeTimeStr = alarm.wakeTime else {
                cancelAlarm()
                return
            }

            guard let wakeTime = parseWakeTime(wakeTimeStr) else {
                error = "Invalid wake time format from backend"
                return
            }

            scheduleAlarm(at: wakeTime)
        } catch {
            // Config endpoint may not have alarm key yet — try calendar fallback
            await syncAlarmFromCalendar()
        }
    }

    // MARK: - Calculate optimal wake time from calendar

    func syncAlarmFromCalendar(
        defaultPrepMinutes: Int = 45,
        defaultCommuteMinutes: Int = 20
    ) async {
        await eventKit.fetchTodayEvents()
        let events = eventKit.todayEvents

        // Find first non-all-day event tomorrow morning
        let calendar = Calendar.current
        let now = Date()
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: now)!
        let tomorrowStart = calendar.startOfDay(for: tomorrow)
        let tomorrowNoon = calendar.date(bySettingHour: 12, minute: 0, second: 0, of: tomorrow)!

        let tomorrowEvents = eventKit.fetchEvents(from: tomorrowStart, to: tomorrowNoon)
            .filter { !$0.isAllDay }
            .sorted { $0.start < $1.start }

        guard let firstEvent = tomorrowEvents.first else {
            // No morning events — no alarm
            cancelAlarm()
            return
        }

        // Wake time = event start - commute - prep
        let totalLeadMinutes = defaultPrepMinutes + defaultCommuteMinutes
        guard let wakeTime = calendar.date(byAdding: .minute, value: -totalLeadMinutes, to: firstEvent.start) else {
            return
        }

        // Clamp to reasonable range (5:00 AM - 10:00 AM)
        let minWake = calendar.date(bySettingHour: 5, minute: 0, second: 0, of: tomorrow)!
        let maxWake = calendar.date(bySettingHour: 10, minute: 0, second: 0, of: tomorrow)!
        let clampedWake = max(minWake, min(maxWake, wakeTime))

        scheduleAlarm(at: clampedWake)
    }

    // MARK: - Schedule local notification alarm

    func scheduleAlarm(at time: Date) {
        guard time > Date() else {
            error = "Alarm time is in the past"
            return
        }

        // Cancel any existing alarm
        cancelNotification()

        let content = UNMutableNotificationContent()
        content.title = "T.A.R.S."
        content.body = "Time to wake up. Your briefing is ready."
        content.categoryIdentifier = Self.alarmCategoryIdentifier
        content.sound = UNNotificationSound(named: UNNotificationSoundName("alarm.caf"))
        content.interruptionLevel = .timeSensitive
        content.userInfo = ["type": "smart_alarm"]

        let calendar = Calendar.current
        let components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: time)
        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)

        let request = UNNotificationRequest(
            identifier: Self.notificationIdentifier,
            content: content,
            trigger: trigger
        )

        UNUserNotificationCenter.current().add(request) { [weak self] err in
            Task { @MainActor in
                if let err {
                    self?.error = "Failed to schedule alarm: \(err.localizedDescription)"
                } else {
                    self?.scheduledAlarmTime = time
                    self?.isSnoozed = false
                    self?.error = nil
                }
            }
        }
    }

    // MARK: - Alarm actions

    func dismissAlarm() {
        cancelNotification()
        isAlarmActive = false
        isSnoozed = false
        scheduledAlarmTime = nil
    }

    func snoozeAlarm() {
        cancelNotification()
        isSnoozed = true
        isAlarmActive = false

        let snoozeTime = Date().addingTimeInterval(TimeInterval(Self.snoozeDurationMinutes * 60))
        scheduleAlarm(at: snoozeTime)
    }

    func cancelAlarm() {
        cancelNotification()
        scheduledAlarmTime = nil
        isAlarmActive = false
        isSnoozed = false
    }

    func handleAlarmFired() {
        isAlarmActive = true
        isSnoozed = false
        configureAudioForAlarm()
    }

    // MARK: - Post-dismiss: fetch briefing + TTS

    func startBriefingAfterDismiss() async -> Briefing? {
        do {
            let briefing: Briefing = try await api.getBriefing(type: "morning")
            routeToAirPlayIfAvailable()
            speech.speakBriefing(briefing.narrative)
            return briefing
        } catch {
            self.error = "Failed to load briefing: \(error.localizedDescription)"
            return nil
        }
    }

    // MARK: - AirPlay routing

    func routeToAirPlayIfAvailable() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .spokenAudio, options: [.allowAirPlay])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            // Non-fatal — falls back to device speaker
        }

        // Check if an AirPlay route is already selected (e.g., HomePod)
        let currentRoute = session.currentRoute
        let hasAirPlay = currentRoute.outputs.contains { $0.portType == .airPlay }
        if !hasAirPlay {
            // Prompt user to pick AirPlay device via system route picker
            // (handled in AlarmView with AVRoutePickerView)
        }
    }

    // MARK: - Helpers

    private func parseWakeTime(_ str: String) -> Date? {
        let calendar = Calendar.current
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: Date())!

        // Parse "HH:mm" format
        let parts = str.split(separator: ":")
        guard parts.count == 2,
              let hour = Int(parts[0]),
              let minute = Int(parts[1])
        else { return nil }

        return calendar.date(bySettingHour: hour, minute: minute, second: 0, of: tomorrow)
    }

    private func cancelNotification() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(
            withIdentifiers: [Self.notificationIdentifier]
        )
        UNUserNotificationCenter.current().removeDeliveredNotifications(
            withIdentifiers: [Self.notificationIdentifier]
        )
    }

    private func configureAudioForAlarm() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .default, options: [.allowAirPlay])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            // Non-fatal
        }
    }

    // MARK: - Formatted time display

    var alarmTimeString: String? {
        guard let time = scheduledAlarmTime else { return nil }
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: time)
    }

    var alarmDateString: String? {
        guard let time = scheduledAlarmTime else { return nil }
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMM d"
        return formatter.string(from: time)
    }
}
