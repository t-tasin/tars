import Foundation
import AVFoundation
import Speech

// MARK: - SpeechService

@MainActor
final class SpeechService: ObservableObject {
    static let shared = SpeechService()

    // TTS state
    @Published var isSpeaking = false

    // STT state
    @Published var isListening = false
    @Published var transcribedText = ""
    @Published var error: String?

    private let synthesizer = AVSpeechSynthesizer()
    private var ttsDelegate: TTSDelegate?

    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let audioEngine = AVAudioEngine()

    private init() {
        ttsDelegate = TTSDelegate { [weak self] in
            Task { @MainActor in
                self?.isSpeaking = false
            }
        }
        synthesizer.delegate = ttsDelegate
    }

    // MARK: - TTS: Text-to-Speech

    /// Speak text aloud using AVSpeechSynthesizer.
    func speak(_ text: String, rate: Float = AVSpeechUtteranceDefaultSpeechRate) {
        stopSpeaking()

        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = rate
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.pitchMultiplier = 1.0
        utterance.preUtteranceDelay = 0.1

        configureAudioSession(forPlayback: true)
        synthesizer.speak(utterance)
        isSpeaking = true
    }

    /// Speak a briefing narrative — slightly slower for comprehension.
    func speakBriefing(_ narrative: String) {
        speak(narrative, rate: AVSpeechUtteranceDefaultSpeechRate * 0.9)
    }

    func stopSpeaking() {
        guard isSpeaking else { return }
        synthesizer.stopSpeaking(at: .immediate)
        isSpeaking = false
    }

    func toggleSpeech(for text: String) {
        if isSpeaking {
            stopSpeaking()
        } else {
            speak(text)
        }
    }

    // MARK: - STT: Speech-to-Text

    /// Request microphone + speech recognition permissions.
    func requestSTTAuthorization() async -> Bool {
        let speechStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }

        guard speechStatus == .authorized else {
            error = "Speech recognition not authorized"
            return false
        }

        let micStatus = await AVAudioApplication.requestRecordPermission()
        guard micStatus else {
            error = "Microphone access denied"
            return false
        }

        return true
    }

    /// Start listening for voice input. Transcription updates `transcribedText` in real time.
    func startListening() async {
        guard !isListening else { return }
        guard speechRecognizer?.isAvailable == true else {
            error = "Speech recognition unavailable"
            return
        }

        let authorized = await requestSTTAuthorization()
        guard authorized else { return }

        error = nil
        transcribedText = ""

        do {
            try startRecognition()
            isListening = true
        } catch {
            self.error = "Failed to start listening: \(error.localizedDescription)"
        }
    }

    /// Stop listening and return the final transcription.
    @discardableResult
    func stopListening() -> String {
        guard isListening else { return transcribedText }

        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()

        recognitionRequest = nil
        recognitionTask = nil
        isListening = false

        return transcribedText
    }

    /// Push-to-talk: start on press, stop on release, return transcription.
    func pushToTalkRelease() -> String {
        stopListening()
    }

    // MARK: - Recognition internals

    private func startRecognition() throws {
        // Cancel any in-progress task
        recognitionTask?.cancel()
        recognitionTask = nil

        configureAudioSession(forPlayback: false)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        self.recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { buffer, _ in
            request.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()

        recognitionTask = speechRecognizer?.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor [weak self] in
                guard let self else { return }

                if let result {
                    self.transcribedText = result.bestTranscription.formattedString
                }

                if error != nil || (result?.isFinal == true) {
                    self.audioEngine.stop()
                    inputNode.removeTap(onBus: 0)
                    self.recognitionRequest = nil
                    self.recognitionTask = nil
                    self.isListening = false
                }
            }
        }
    }

    // MARK: - Audio session

    private func configureAudioSession(forPlayback: Bool) {
        let session = AVAudioSession.sharedInstance()
        do {
            if forPlayback {
                try session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
            } else {
                try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
            }
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            // Non-fatal — audio may still work
        }
    }
}

// MARK: - TTS Delegate

private final class TTSDelegate: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
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
