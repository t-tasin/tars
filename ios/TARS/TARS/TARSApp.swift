import SwiftUI

@main
struct TARSApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var alarmService = AlarmService.shared

    var body: some Scene {
        WindowGroup {
            ZStack {
                MainTabView()

                if alarmService.isAlarmActive {
                    AlarmView(alarmService: alarmService)
                        .transition(.opacity)
                        .zIndex(100)
                }
            }
            .animation(.easeInOut(duration: 0.3), value: alarmService.isAlarmActive)
            .task {
                await alarmService.syncAlarmFromBackend()
            }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        registerForPushNotifications()
        AlarmService.shared.registerAlarmCategory()
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
        KeychainService.shared.save(token, forKey: .deviceToken)
        registerTokenWithBackend(token)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        print("Push registration failed: \(error.localizedDescription)")
    }

    // MARK: - UNUserNotificationCenterDelegate

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .badge, .sound]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let actionIdentifier = response.actionIdentifier
        let userInfo = response.notification.request.content.userInfo

        // Smart alarm notifications
        if userInfo["type"] as? String == "smart_alarm" {
            switch actionIdentifier {
            case AlarmService.dismissActionIdentifier, UNNotificationDefaultActionIdentifier:
                await MainActor.run { AlarmService.shared.handleAlarmFired() }
            case AlarmService.snoozeActionIdentifier:
                await MainActor.run { AlarmService.shared.snoozeAlarm() }
            default:
                await MainActor.run { AlarmService.shared.handleAlarmFired() }
            }
            return
        }

        // Workout reminder notifications
        if userInfo["type"] as? String == "workout_reminder" {
            let sessionId = userInfo["session_id"] as? String
            switch actionIdentifier {
            case "START_WORKOUT":
                if let sessionId {
                    await handleWorkoutStart(sessionId: sessionId)
                }
            case "SKIP_WORKOUT":
                // Skip action — the app will prompt for a reason in the UI
                break
            default:
                break
            }
            return
        }

        // Approval notifications
        guard let approvalId = userInfo["approval_id"] as? String else { return }

        switch actionIdentifier {
        case "APPROVE_ACTION":
            await handleApprovalAction(approvalId: approvalId, approve: true)
        case "REJECT_ACTION":
            await handleApprovalAction(approvalId: approvalId, approve: false)
        case UNNotificationDefaultActionIdentifier:
            break
        default:
            break
        }
    }

    // MARK: - Approval action handler

    private func handleApprovalAction(approvalId: String, approve: Bool) async {
        do {
            if approve {
                let _: ApprovalActionResponse = try await APIClient.shared.approveAction(id: approvalId)
            } else {
                let _: ApprovalActionResponse = try await APIClient.shared.rejectAction(id: approvalId)
            }
        } catch {
            print("Approval action failed for \(approvalId): \(error.localizedDescription)")
        }
    }

    // MARK: - Workout action handler

    private func handleWorkoutStart(sessionId: String) async {
        do {
            let _: [String: String] = try await APIClient.shared.request(.startSession(sessionId))
        } catch {
            print("Workout start failed for \(sessionId): \(error.localizedDescription)")
        }
    }

    // MARK: - Device token registration

    private func registerTokenWithBackend(_ token: String) {
        Task {
            do {
                try await APIClient.shared.registerDeviceToken(
                    token: token,
                    deviceName: UIDevice.current.name,
                    platform: "ios"
                )
            } catch {
                print("Device token registration failed: \(error.localizedDescription)")
            }
        }
    }

    // MARK: - Push notification setup

    private func registerForPushNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(
            options: [.alert, .badge, .sound]
        ) { granted, _ in
            guard granted else { return }

            let approveAction = UNNotificationAction(
                identifier: "APPROVE_ACTION",
                title: "Approve",
                options: [.authenticationRequired]
            )
            let rejectAction = UNNotificationAction(
                identifier: "REJECT_ACTION",
                title: "Reject",
                options: [.destructive, .authenticationRequired]
            )
            let approvalCategory = UNNotificationCategory(
                identifier: "APPROVAL",
                actions: [approveAction, rejectAction],
                intentIdentifiers: []
            )
            let startWorkoutAction = UNNotificationAction(
                identifier: "START_WORKOUT",
                title: "Start",
                options: [.foreground]
            )
            let skipWorkoutAction = UNNotificationAction(
                identifier: "SKIP_WORKOUT",
                title: "Skip",
                options: [.destructive, .foreground]
            )
            let workoutCategory = UNNotificationCategory(
                identifier: "WORKOUT_REMINDER",
                actions: [startWorkoutAction, skipWorkoutAction],
                intentIdentifiers: []
            )
            UNUserNotificationCenter.current().setNotificationCategories([approvalCategory, workoutCategory])

            DispatchQueue.main.async {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }
}
