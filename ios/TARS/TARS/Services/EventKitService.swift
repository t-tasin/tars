import Foundation
import EventKit

// MARK: - Local event model

struct LocalCalendarEvent: Identifiable, Sendable {
    let id: String
    let title: String
    let start: Date
    let end: Date
    let location: String?
    let calendarName: String
    let calendarColor: String
    let isAllDay: Bool
    let notes: String?

    var timeString: String {
        if isAllDay { return "All day" }
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return "\(formatter.string(from: start)) – \(formatter.string(from: end))"
    }
}

// MARK: - EventKitService

@MainActor
final class EventKitService: ObservableObject {
    static let shared = EventKitService()

    @Published var isAuthorized = false
    @Published var todayEvents: [LocalCalendarEvent] = []
    @Published var error: String?

    private let store = EKEventStore()

    private init() {
        checkAuthorizationStatus()
    }

    // MARK: - Authorization

    private func checkAuthorizationStatus() {
        let status = EKEventStore.authorizationStatus(for: .event)
        if #available(iOS 17.0, *) {
            isAuthorized = status == .fullAccess
        } else {
            isAuthorized = status == .authorized
        }
    }

    func requestAccess() async {
        do {
            let granted: Bool
            if #available(iOS 17.0, *) {
                granted = try await store.requestFullAccessToEvents()
            } else {
                granted = try await store.requestAccess(to: .event)
            }
            isAuthorized = granted
            if !granted {
                error = "Calendar access denied"
            }
        } catch {
            self.error = "Calendar access failed: \(error.localizedDescription)"
        }
    }

    // MARK: - Read events

    /// Fetch today's events from all calendars.
    func fetchTodayEvents() async {
        if !isAuthorized {
            await requestAccess()
        }
        guard isAuthorized else { return }

        let calendar = Calendar.current
        let startOfDay = calendar.startOfDay(for: Date())
        guard let endOfDay = calendar.date(byAdding: .day, value: 1, to: startOfDay) else { return }

        let predicate = store.predicateForEvents(withStart: startOfDay, end: endOfDay, calendars: nil)
        let ekEvents = store.events(matching: predicate)

        todayEvents = ekEvents
            .sorted { $0.startDate < $1.startDate }
            .map { event in
                LocalCalendarEvent(
                    id: event.eventIdentifier,
                    title: event.title ?? "Untitled",
                    start: event.startDate,
                    end: event.endDate,
                    location: event.location,
                    calendarName: event.calendar.title,
                    calendarColor: event.calendar.cgColor?.hexString ?? "#007AFF",
                    isAllDay: event.isAllDay,
                    notes: event.notes
                )
            }
    }

    /// Fetch events for a specific date range.
    func fetchEvents(from start: Date, to end: Date) -> [LocalCalendarEvent] {
        guard isAuthorized else { return [] }

        let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        return store.events(matching: predicate)
            .sorted { $0.startDate < $1.startDate }
            .map { event in
                LocalCalendarEvent(
                    id: event.eventIdentifier,
                    title: event.title ?? "Untitled",
                    start: event.startDate,
                    end: event.endDate,
                    location: event.location,
                    calendarName: event.calendar.title,
                    calendarColor: event.calendar.cgColor?.hexString ?? "#007AFF",
                    isAllDay: event.isAllDay,
                    notes: event.notes
                )
            }
    }

    // MARK: - Create event (with user confirmation via EKEventEditViewController)

    /// Creates an event in the default calendar. Returns the event identifier on success.
    ///
    /// This is a programmatic creation — callers should confirm with the user before invoking.
    /// For UI-driven creation, use `EKEventEditViewController` in a SwiftUI wrapper.
    func createEvent(
        title: String,
        startDate: Date,
        endDate: Date,
        location: String? = nil,
        notes: String? = nil,
        calendarIdentifier: String? = nil
    ) async throws -> String {
        if !isAuthorized {
            await requestAccess()
        }
        guard isAuthorized else {
            throw EventKitError.notAuthorized
        }

        let event = EKEvent(eventStore: store)
        event.title = title
        event.startDate = startDate
        event.endDate = endDate
        event.location = location
        event.notes = notes

        if let calId = calendarIdentifier,
           let cal = store.calendar(withIdentifier: calId) {
            event.calendar = cal
        } else {
            event.calendar = store.defaultCalendarForNewEvents
        }

        try store.save(event, span: .thisEvent)

        // Refresh today's events if the new event falls today
        let calendar = Calendar.current
        if calendar.isDateInToday(startDate) || calendar.isDateInToday(endDate) {
            await fetchTodayEvents()
        }

        return event.eventIdentifier
    }

    // MARK: - Available calendars

    var calendars: [EKCalendar] {
        store.calendars(for: .event)
    }
}

// MARK: - Errors

enum EventKitError: LocalizedError {
    case notAuthorized

    var errorDescription: String? {
        switch self {
        case .notAuthorized:
            return "Calendar access not authorized"
        }
    }
}

// MARK: - CGColor hex helper

private extension CGColor {
    var hexString: String {
        guard let components = components, components.count >= 3 else { return "#007AFF" }
        let r = Int(components[0] * 255)
        let g = Int(components[1] * 255)
        let b = Int(components[2] * 255)
        return String(format: "#%02X%02X%02X", r, g, b)
    }
}
