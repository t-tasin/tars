import SwiftUI
import AVKit

// MARK: - AlarmView

struct AlarmView: View {
    @ObservedObject var alarmService = AlarmService.shared
    @StateObject private var briefingViewModel = BriefingViewModel()
    @State private var phase: AlarmPhase = .ringing
    @State private var pulseScale: CGFloat = 1.0
    @State private var showBriefing = false
    @State private var showRoutePickerHint = false

    enum AlarmPhase {
        case ringing
        case loading
        case briefing
    }

    var body: some View {
        ZStack {
            backgroundGradient

            switch phase {
            case .ringing:
                alarmRingingView
            case .loading:
                loadingView
            case .briefing:
                briefingPlaybackView
            }
        }
        .ignoresSafeArea()
        .onAppear {
            startPulseAnimation()
        }
    }

    // MARK: - Background

    private var backgroundGradient: some View {
        LinearGradient(
            colors: phase == .ringing
                ? [Color(red: 0.08, green: 0.08, blue: 0.15), Color(red: 0.12, green: 0.10, blue: 0.25)]
                : [Color(red: 0.05, green: 0.10, blue: 0.18), Color(red: 0.10, green: 0.15, blue: 0.28)],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    // MARK: - Alarm ringing

    private var alarmRingingView: some View {
        VStack(spacing: 32) {
            Spacer()

            // Time display
            Text(currentTimeString)
                .font(.system(size: 72, weight: .thin, design: .rounded))
                .foregroundStyle(.white)
                .monospacedDigit()

            Text(currentDateString)
                .font(.title3)
                .foregroundStyle(.white.opacity(0.6))

            Spacer()

            // Pulse ring
            ZStack {
                Circle()
                    .stroke(.white.opacity(0.1), lineWidth: 2)
                    .frame(width: 180, height: 180)
                    .scaleEffect(pulseScale)
                    .opacity(2.0 - Double(pulseScale))

                Circle()
                    .stroke(.white.opacity(0.15), lineWidth: 1)
                    .frame(width: 140, height: 140)

                Image(systemName: "alarm.fill")
                    .font(.system(size: 48))
                    .foregroundStyle(.white)
                    .symbolEffect(.bounce.byLayer, options: .repeating)
            }

            Spacer()

            // Action buttons
            VStack(spacing: 16) {
                // Dismiss button
                Button {
                    dismiss()
                } label: {
                    Text("Dismiss")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .background(.white.opacity(0.15))
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .overlay(
                            RoundedRectangle(cornerRadius: 16)
                                .stroke(.white.opacity(0.2), lineWidth: 1)
                        )
                }

                // Snooze button
                Button {
                    snooze()
                } label: {
                    Text("Snooze — 5 min")
                        .font(.body.weight(.medium))
                        .foregroundStyle(.white.opacity(0.7))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
            }
            .padding(.horizontal, 40)
            .padding(.bottom, 60)
        }
    }

    // MARK: - Loading state

    private var loadingView: some View {
        VStack(spacing: 24) {
            Spacer()

            ProgressView()
                .scaleEffect(1.5)
                .tint(.white)

            Text("Loading your briefing...")
                .font(.title3)
                .foregroundStyle(.white.opacity(0.8))

            Spacer()
        }
    }

    // MARK: - Briefing playback

    private var briefingPlaybackView: some View {
        VStack(spacing: 20) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(briefingViewModel.greeting)
                        .font(.title.weight(.bold))
                        .foregroundStyle(.white)

                    Text(briefingViewModel.dateString)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.6))
                }
                Spacer()

                // AirPlay route picker
                AirPlayButton()
                    .frame(width: 36, height: 36)
                    .tint(.white)
            }
            .padding(.horizontal, 24)
            .padding(.top, 60)

            // TTS indicator
            if SpeechService.shared.isSpeaking {
                HStack(spacing: 10) {
                    Image(systemName: "waveform")
                        .symbolEffect(.variableColor.iterative)
                        .foregroundStyle(.cyan)

                    Text("Reading briefing aloud...")
                        .font(.callout)
                        .foregroundStyle(.white.opacity(0.7))

                    Spacer()

                    Button("Stop") {
                        SpeechService.shared.stopSpeaking()
                    }
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.red.opacity(0.8))
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                .background(.white.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal, 24)
            }

            // Briefing sections
            ScrollView {
                VStack(spacing: 10) {
                    ForEach(briefingViewModel.sections) { parsed in
                        alarmBriefingSection(parsed)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 40)
            }
        }
    }

    private func alarmBriefingSection(_ parsed: ParsedBriefingSection) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: parsed.section.icon)
                    .foregroundStyle(sectionColor(parsed.section))
                    .font(.callout)

                Text(parsed.section.title)
                    .font(.headline)
                    .foregroundStyle(.white)
            }

            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(parsed.content.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.75))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func sectionColor(_ section: BriefingSection) -> Color {
        switch section {
        case .weather:  return .cyan
        case .schedule: return .orange
        case .email:    return .indigo
        case .health:   return .red
        case .finance:  return .green
        case .jobs:     return .purple
        }
    }

    // MARK: - Actions

    private func dismiss() {
        withAnimation(.easeInOut(duration: 0.3)) {
            phase = .loading
        }
        alarmService.dismissAlarm()

        Task {
            if let briefing = await alarmService.startBriefingAfterDismiss() {
                briefingViewModel.briefing = briefing
                withAnimation(.easeInOut(duration: 0.4)) {
                    phase = .briefing
                }
            } else {
                // Briefing failed — still transition to show error
                withAnimation(.easeInOut(duration: 0.4)) {
                    phase = .briefing
                }
            }
        }
    }

    private func snooze() {
        alarmService.snoozeAlarm()
    }

    // MARK: - Helpers

    private var currentTimeString: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm"
        return formatter.string(from: Date())
    }

    private var currentDateString: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMMM d"
        return formatter.string(from: Date())
    }

    private func startPulseAnimation() {
        withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
            pulseScale = 1.3
        }
    }
}

// MARK: - AirPlay Route Picker (UIKit bridge)

struct AirPlayButton: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.activeTintColor = .systemCyan
        picker.tintColor = .white
        picker.prioritizesVideoDevices = false
        return picker
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}

// MARK: - Alarm status (for settings / main tab)

struct AlarmStatusView: View {
    @ObservedObject var alarmService = AlarmService.shared

    var body: some View {
        if let time = alarmService.alarmTimeString {
            HStack(spacing: 10) {
                Image(systemName: "alarm.fill")
                    .foregroundStyle(.cyan)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Smart Alarm")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)

                    Text(time)
                        .font(.title3.weight(.semibold))
                        .monospacedDigit()
                }

                Spacer()

                if alarmService.isSnoozed {
                    Text("Snoozed")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(.orange.opacity(0.12))
                        .clipShape(Capsule())
                }

                Button("Cancel") {
                    alarmService.cancelAlarm()
                }
                .font(.caption.weight(.medium))
                .foregroundStyle(.red)
            }
            .padding(12)
            .background(Color(.systemGray6))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }
}

#Preview("Alarm Ringing") {
    AlarmView()
}

#Preview("Alarm Status") {
    AlarmStatusView()
        .padding()
}
