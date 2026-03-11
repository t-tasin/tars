import SwiftUI

struct BriefingView: View {
    @StateObject private var viewModel = BriefingViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.briefing == nil {
                    ProgressView("Loading briefing...")
                } else if let error = viewModel.error, viewModel.briefing == nil {
                    ContentUnavailableView {
                        Label("Failed to Load", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(error)
                    } actions: {
                        Button("Retry") { viewModel.fetchBriefing() }
                            .buttonStyle(.bordered)
                    }
                } else if viewModel.briefing != nil {
                    briefingContent
                } else {
                    ContentUnavailableView(
                        "No Briefing Yet",
                        systemImage: "sun.max",
                        description: Text("Your morning briefing will appear here.")
                    )
                }
            }
            .navigationTitle("Briefing")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if viewModel.briefing != nil {
                        Button(action: viewModel.toggleSpeech) {
                            Image(systemName: viewModel.isSpeaking ? "stop.circle.fill" : "play.circle.fill")
                                .font(.title3)
                                .foregroundStyle(viewModel.isSpeaking ? .red : .blue)
                        }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: viewModel.fetchBriefing) {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(viewModel.isLoading)
                }
            }
            .task {
                viewModel.fetchBriefing()
            }
        }
    }

    // MARK: - Briefing content

    private var briefingContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Header
                VStack(alignment: .leading, spacing: 4) {
                    Text(viewModel.greeting)
                        .font(.largeTitle.weight(.bold))

                    Text(viewModel.dateString)
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal)
                .padding(.top, 8)

                // TTS indicator
                if viewModel.isSpeaking {
                    HStack(spacing: 8) {
                        Image(systemName: "waveform")
                            .symbolEffect(.variableColor.iterative)
                            .foregroundStyle(.blue)

                        Text("Reading briefing aloud...")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        Spacer()

                        Button("Stop") {
                            viewModel.toggleSpeech()
                        }
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.red)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                    .background(.blue.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .padding(.horizontal)
                }

                // Sections
                VStack(spacing: 10) {
                    ForEach(viewModel.sections) { parsed in
                        BriefingSectionView(
                            section: parsed.section,
                            content: parsed.content,
                            isExpanded: viewModel.expandedSections.contains(parsed.section),
                            onToggle: {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    viewModel.toggleSection(parsed.section)
                                }
                            }
                        )
                    }
                }
                .padding(.horizontal)

                // Delivered badge
                if let briefing = viewModel.briefing, briefing.delivered {
                    HStack {
                        Spacer()
                        Label("Delivered", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                            .font(.caption)
                        Spacer()
                    }
                    .padding(.top, 4)
                }
            }
            .padding(.bottom, 24)
        }
    }
}

#Preview {
    BriefingView()
}
