import Foundation

@MainActor
final class WorkoutViewModel: ObservableObject {
    @Published var session: WorkoutSessionResponse?
    @Published var streak: Int = 0
    @Published var isLoading = false
    @Published var error: String?

    private let api = APIClient.shared

    func loadTodaySession() async {
        isLoading = true
        error = nil

        do {
            session = try await api.request(.todaySession())
        } catch {
            self.error = "No workout scheduled today"
            session = nil
        }

        isLoading = false
    }

    func startSession() async {
        guard let sessionId = session?.id else { return }
        do {
            let _: [String: String] = try await api.request(.startSession(sessionId))
            await loadTodaySession()
        } catch {
            self.error = "Failed to start session: \(error.localizedDescription)"
        }
    }

    func skipSession(reason: String) async {
        guard let sessionId = session?.id else { return }
        do {
            let _: [String: String] = try await api.request(.skipSession(sessionId, reason: reason))
            await loadTodaySession()
        } catch {
            self.error = "Failed to skip session: \(error.localizedDescription)"
        }
    }

    func logSet(exerciseId: String, setNumber: Int, reps: Int, weight: Double) async {
        guard let sessionId = session?.id else { return }
        let body = LogSetBody(
            sessionId: sessionId,
            exerciseId: exerciseId,
            setNumber: setNumber,
            actualReps: reps,
            actualWeight: weight
        )
        do {
            let _: [String: String] = try await api.request(.logSet(body))
            await loadTodaySession()
        } catch {
            self.error = "Failed to log set: \(error.localizedDescription)"
        }
    }

    func completeSession() async {
        guard let sessionId = session?.id else { return }
        do {
            let _: [String: String] = try await api.request(.completeSession(sessionId))
            await loadTodaySession()
            await loadStreak()
        } catch {
            self.error = "Failed to complete session: \(error.localizedDescription)"
        }
    }

    func loadStreak() async {
        do {
            let response: StreakResponseModel = try await api.request(.workoutStreak)
            streak = response.streak
        } catch {
            streak = 0
        }
    }
}
