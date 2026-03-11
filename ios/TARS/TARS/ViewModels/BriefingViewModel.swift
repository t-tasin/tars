import Foundation
import AVFoundation

@MainActor
final class BriefingViewModel: ObservableObject {
    @Published var briefing: Briefing?
    @Published var isLoading = false
    @Published var error: String?
    @Published var isSpeaking = false
    @Published var expandedSections: Set<BriefingSection> = Set(BriefingSection.allCases)

    private let api = APIClient.shared
    private let synthesizer = AVSpeechSynthesizer()
    private var speechDelegate: SpeechDelegate?

    init() {
        speechDelegate = SpeechDelegate { [weak self] in
            Task { @MainActor in
                self?.isSpeaking = false
            }
        }
        synthesizer.delegate = speechDelegate
    }

    // MARK: - Sections parsed from payload

    var sections: [ParsedBriefingSection] {
        guard let briefing else { return [] }
        return BriefingSection.allCases.compactMap { section in
            guard let data = briefing.payload[section.payloadKey] else { return nil }
            return ParsedBriefingSection(
                section: section,
                content: extractContent(from: data, section: section)
            )
        }
    }

    var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        if hour < 12 {
            return "Good morning, Tasin"
        } else if hour < 17 {
            return "Good afternoon, Tasin"
        } else {
            return "Good evening, Tasin"
        }
    }

    var dateString: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMMM d"
        return formatter.string(from: Date())
    }

    // MARK: - Fetch

    func fetchBriefing() {
        guard !isLoading else { return }
        isLoading = true
        error = nil

        Task {
            do {
                briefing = try await api.getBriefing(type: "morning")
            } catch {
                self.error = error.localizedDescription
            }
            isLoading = false
        }
    }

    // MARK: - TTS

    func toggleSpeech() {
        if isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
            isSpeaking = false
        } else {
            guard let narrative = briefing?.narrative, !narrative.isEmpty else { return }
            let utterance = AVSpeechUtterance(string: narrative)
            utterance.rate = AVSpeechUtteranceDefaultSpeechRate
            utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
            utterance.pitchMultiplier = 1.0
            synthesizer.speak(utterance)
            isSpeaking = true
        }
    }

    // MARK: - Section toggling

    func toggleSection(_ section: BriefingSection) {
        if expandedSections.contains(section) {
            expandedSections.remove(section)
        } else {
            expandedSections.insert(section)
        }
    }

    // MARK: - Content extraction

    private func extractContent(from value: AnyCodable, section: BriefingSection) -> [String] {
        // Payload values can be strings, arrays of strings, or dicts
        if let str = value.value as? String {
            return [str]
        }
        if let arr = value.value as? [AnyCodable] {
            return arr.compactMap { item in
                if let s = item.value as? String { return s }
                if let dict = item.value as? [String: AnyCodable] {
                    return formatDict(dict, for: section)
                }
                return nil
            }
        }
        if let dict = value.value as? [String: AnyCodable] {
            return formatTopLevelDict(dict, for: section)
        }
        return []
    }

    private func formatDict(_ dict: [String: AnyCodable], for section: BriefingSection) -> String {
        switch section {
        case .schedule:
            let title = dict["title"]?.value as? String ?? "Event"
            let time = dict["time"]?.value as? String ?? dict["start"]?.value as? String ?? ""
            return time.isEmpty ? title : "\(time) — \(title)"
        case .email:
            let from = dict["from"]?.value as? String ?? dict["sender"]?.value as? String ?? ""
            let subject = dict["subject"]?.value as? String ?? "No subject"
            return from.isEmpty ? subject : "\(from): \(subject)"
        case .jobs:
            let company = dict["company"]?.value as? String ?? ""
            let role = dict["title"]?.value as? String ?? dict["role"]?.value as? String ?? "Role"
            return company.isEmpty ? role : "\(role) at \(company)"
        default:
            return dict.map { "\($0.key): \(stringValue($0.value))" }.joined(separator: ", ")
        }
    }

    private func formatTopLevelDict(_ dict: [String: AnyCodable], for section: BriefingSection) -> [String] {
        switch section {
        case .weather:
            var lines: [String] = []
            if let temp = dict["temperature"]?.value { lines.append("Temperature: \(temp)") }
            if let condition = dict["condition"]?.value as? String { lines.append(condition) }
            if let high = dict["high"]?.value, let low = dict["low"]?.value {
                lines.append("High: \(high) / Low: \(low)")
            }
            if let summary = dict["summary"]?.value as? String { lines.append(summary) }
            if lines.isEmpty {
                return dict.map { "\($0.key): \(stringValue($0.value))" }
            }
            return lines
        case .finance:
            var lines: [String] = []
            if let spent = dict["spent_today"]?.value { lines.append("Spent today: $\(spent)") }
            if let budget = dict["budget_remaining"]?.value { lines.append("Budget remaining: $\(budget)") }
            if let summary = dict["summary"]?.value as? String { lines.append(summary) }
            if lines.isEmpty {
                return dict.map { "\($0.key): \(stringValue($0.value))" }
            }
            return lines
        case .health:
            var lines: [String] = []
            if let steps = dict["steps"]?.value { lines.append("Steps: \(steps)") }
            if let sleep = dict["sleep"]?.value { lines.append("Sleep: \(sleep)") }
            if let summary = dict["summary"]?.value as? String { lines.append(summary) }
            if lines.isEmpty {
                return dict.map { "\($0.key): \(stringValue($0.value))" }
            }
            return lines
        default:
            return dict.map { "\($0.key): \(stringValue($0.value))" }
        }
    }

    private func stringValue(_ value: AnyCodable) -> String {
        if let s = value.value as? String { return s }
        if let i = value.value as? Int { return "\(i)" }
        if let d = value.value as? Double { return String(format: "%.1f", d) }
        if let b = value.value as? Bool { return b ? "Yes" : "No" }
        return "—"
    }
}

// MARK: - Section types

enum BriefingSection: String, CaseIterable, Hashable {
    case weather
    case schedule
    case email
    case health
    case finance
    case jobs

    var payloadKey: String { rawValue }

    var title: String {
        switch self {
        case .weather:  return "Weather"
        case .schedule: return "Schedule"
        case .email:    return "Email"
        case .health:   return "Health"
        case .finance:  return "Finance"
        case .jobs:     return "Jobs"
        }
    }

    var icon: String {
        switch self {
        case .weather:  return "cloud.sun.fill"
        case .schedule: return "calendar"
        case .email:    return "envelope.fill"
        case .health:   return "heart.fill"
        case .finance:  return "dollarsign.circle.fill"
        case .jobs:     return "briefcase.fill"
        }
    }

    var tintColor: String {
        switch self {
        case .weather:  return "blue"
        case .schedule: return "orange"
        case .email:    return "indigo"
        case .health:   return "red"
        case .finance:  return "green"
        case .jobs:     return "purple"
        }
    }
}

struct ParsedBriefingSection: Identifiable {
    let section: BriefingSection
    let content: [String]

    var id: String { section.rawValue }
}

// MARK: - Speech delegate

private final class SpeechDelegate: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    let onFinish: () -> Void

    init(onFinish: @escaping () -> Void) {
        self.onFinish = onFinish
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        onFinish()
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        onFinish()
    }
}
