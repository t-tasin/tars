import SwiftUI

struct WorkoutSessionView: View {
    @StateObject private var viewModel = WorkoutViewModel()
    @State private var showSkipSheet = false
    @State private var skipReason = ""
    @State private var selectedLog: WorkoutLogResponse?
    @State private var logReps = ""
    @State private var logWeight = ""

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading {
                    ProgressView("Loading workout...")
                } else if let session = viewModel.session {
                    sessionContent(session)
                } else {
                    ContentUnavailableView(
                        "No Workout Today",
                        systemImage: "figure.cooldown",
                        description: Text(viewModel.error ?? "Rest day!")
                    )
                }
            }
            .navigationTitle("Workout")
            .task {
                await viewModel.loadTodaySession()
                await viewModel.loadStreak()
            }
        }
    }

    @ViewBuilder
    private func sessionContent(_ session: WorkoutSessionResponse) -> some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading) {
                    Text(session.dayName.capitalized + " Day")
                        .font(.title2.bold())
                    Text("Streak: \(viewModel.streak) days")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                statusBadge(session.status)
            }
            .padding()

            if session.status == "pending" {
                pendingActions(session)
            } else if session.status == "active" {
                exerciseList(session)
            } else {
                completedView(session)
            }
        }
    }

    @ViewBuilder
    private func pendingActions(_ session: WorkoutSessionResponse) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "figure.strengthtraining.traditional")
                .font(.system(size: 60))
                .foregroundStyle(.blue)

            Text("Ready to go?")
                .font(.title3)

            Button {
                Task { await viewModel.startSession() }
            } label: {
                Text("Start Workout")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(.blue)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .padding(.horizontal)

            Button {
                showSkipSheet = true
            } label: {
                Text("Skip")
                    .foregroundColor(.red)
            }
            Spacer()
        }
        .sheet(isPresented: $showSkipSheet) {
            skipSheet
        }
    }

    @ViewBuilder
    private func exerciseList(_ session: WorkoutSessionResponse) -> some View {
        let grouped = Dictionary(grouping: session.logs) { $0.exerciseId }

        List {
            ForEach(Array(grouped.keys.sorted()), id: \.self) { exerciseId in
                if let sets = grouped[exerciseId] {
                    Section {
                        ForEach(sets, id: \.id) { log in
                            setRow(log)
                        }
                    } header: {
                        Text("Exercise")
                    }
                }
            }

            Section {
                Button {
                    Task { await viewModel.completeSession() }
                } label: {
                    HStack {
                        Spacer()
                        Text("Finish Workout")
                            .font(.headline)
                        Spacer()
                    }
                }
                .tint(.green)
            }
        }
        .sheet(item: $selectedLog) { log in
            logSetSheet(log)
        }
    }

    @ViewBuilder
    private func setRow(_ log: WorkoutLogResponse) -> some View {
        HStack {
            Text("Set \(log.setNumber)")
                .font(.body.bold())

            Spacer()

            if let reps = log.actualReps, let weight = log.actualWeight {
                Text("\(reps) reps @ \(String(format: "%.1f", weight))lbs")
                    .foregroundStyle(.green)
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Text("\(log.targetReps) reps @ \(String(format: "%.1f", log.targetWeight))lbs")
                    .foregroundStyle(.secondary)

                Button("Log") {
                    logReps = "\(log.targetReps)"
                    logWeight = String(format: "%.1f", log.targetWeight)
                    selectedLog = log
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
        }
    }

    @ViewBuilder
    private func completedView(_ session: WorkoutSessionResponse) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.green)
            Text("Workout Complete!")
                .font(.title2.bold())
            Text("Streak: \(viewModel.streak) days")
                .font(.headline)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    private func statusBadge(_ status: String) -> some View {
        Text(status.capitalized)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(statusColor(status).opacity(0.2))
            .foregroundStyle(statusColor(status))
            .clipShape(Capsule())
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "pending": .orange
        case "active": .blue
        case "completed": .green
        case "skipped": .red
        default: .gray
        }
    }

    private var skipSheet: some View {
        NavigationStack {
            Form {
                Section("Why are you skipping?") {
                    TextField("Reason (required)", text: $skipReason)
                }
            }
            .navigationTitle("Skip Workout")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showSkipSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Skip") {
                        Task {
                            await viewModel.skipSession(reason: skipReason)
                            showSkipSheet = false
                            skipReason = ""
                        }
                    }
                    .disabled(skipReason.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func logSetSheet(_ log: WorkoutLogResponse) -> some View {
        NavigationStack {
            Form {
                Section("Set \(log.setNumber)") {
                    HStack {
                        Text("Reps")
                        Spacer()
                        TextField("Reps", text: $logReps)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 80)
                    }
                    HStack {
                        Text("Weight (lbs)")
                        Spacer()
                        TextField("Weight", text: $logWeight)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 80)
                    }
                }
            }
            .navigationTitle("Log Set")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { selectedLog = nil }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if let reps = Int(logReps), let weight = Double(logWeight) {
                            Task {
                                await viewModel.logSet(
                                    exerciseId: log.exerciseId,
                                    setNumber: log.setNumber,
                                    reps: reps,
                                    weight: weight
                                )
                                selectedLog = nil
                            }
                        }
                    }
                }
            }
        }
        .presentationDetents([.medium])
    }
}
