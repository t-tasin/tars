import Foundation

@MainActor
final class ScheduleViewModel: ObservableObject {
    @Published var scheduleResponse: ScheduleResponse?
    @Published var selectedDate = Date()
    @Published var isLoading = false
    @Published var error: String?

    private let api = APIClient.shared

    var events: [CalendarEvent] {
        scheduleResponse?.events ?? []
    }

    var leaveHomeBy: String? {
        scheduleResponse?.leaveHomeBy
    }

    var commuteNote: String? {
        scheduleResponse?.commuteNote
    }

    var dateTitle: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMMM d"
        return formatter.string(from: selectedDate)
    }

    var isToday: Bool {
        Calendar.current.isDateInToday(selectedDate)
    }

    // MARK: - Fetch

    func fetchSchedule() {
        guard !isLoading else { return }
        isLoading = true
        error = nil

        Task {
            do {
                scheduleResponse = try await api.getSchedule(date: selectedDate)
            } catch {
                self.error = error.localizedDescription
            }
            isLoading = false
        }
    }

    func selectDate(_ date: Date) {
        selectedDate = date
        fetchSchedule()
    }

    func goToToday() {
        selectDate(Date())
    }

    // MARK: - Event helpers

    func timeRange(for event: CalendarEvent) -> String {
        if event.allDay { return "All Day" }
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return "\(formatter.string(from: event.start)) – \(formatter.string(from: event.end))"
    }

    func duration(for event: CalendarEvent) -> TimeInterval {
        event.end.timeIntervalSince(event.start)
    }

    func calendarColor(for event: CalendarEvent) -> CalendarColorInfo {
        CalendarColorInfo.from(calendarName: event.calendar)
    }
}

// MARK: - Calendar color mapping

struct CalendarColorInfo {
    let name: String
    let hue: Double

    static func from(calendarName: String) -> CalendarColorInfo {
        // Stable color based on calendar name hash
        let hash = abs(calendarName.hashValue)
        let hue = Double(hash % 360) / 360.0
        return CalendarColorInfo(name: calendarName, hue: hue)
    }
}
