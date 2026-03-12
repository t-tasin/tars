import SwiftUI

struct SplitSetupView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var splitName = ""
    @State private var rotationDays: [String] = ["push", "pull", "legs", "rest"]
    @State private var exercises: [ExerciseEntry] = []
    @State private var newDayName = ""
    @State private var isSaving = false
    @State private var error: String?

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            Form {
                Section("Split Name") {
                    TextField("e.g., Push/Pull/Legs", text: $splitName)
                }

                Section("Rotation") {
                    ForEach(rotationDays.indices, id: \.self) { idx in
                        HStack {
                            Text(rotationDays[idx].capitalized)
                            Spacer()
                            if rotationDays[idx] == "rest" {
                                Text("Rest Day")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .onDelete { indices in
                        rotationDays.remove(atOffsets: indices)
                    }

                    HStack {
                        TextField("Day name", text: $newDayName)
                        Button("Add") {
                            let name = newDayName.lowercased().trimmingCharacters(in: .whitespaces)
                            if !name.isEmpty {
                                rotationDays.append(name)
                                newDayName = ""
                            }
                        }
                        .disabled(newDayName.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                ForEach(workoutDays, id: \.self) { day in
                    Section("\(day.capitalized) Day Exercises") {
                        let dayExercises = exercises.filter { $0.dayName == day }
                        ForEach(dayExercises) { exercise in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(exercise.name).font(.body.bold())
                                Text("\(exercise.sets)x\(exercise.reps) @ \(String(format: "%.1f", exercise.weight))lbs")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        Button("Add Exercise") {
                            exercises.append(ExerciseEntry(dayName: day))
                        }
                    }
                }

                if let error {
                    Section {
                        Text(error).foregroundStyle(.red)
                    }
                }

                Section {
                    Button {
                        Task { await saveSplit() }
                    } label: {
                        HStack {
                            Spacer()
                            if isSaving {
                                ProgressView()
                            } else {
                                Text("Create Split")
                                    .font(.headline)
                            }
                            Spacer()
                        }
                    }
                    .disabled(splitName.isEmpty || exercises.isEmpty || isSaving)
                }
            }
            .navigationTitle("Setup Split")
        }
    }

    private var workoutDays: [String] {
        rotationDays.filter { $0.lowercased() != "rest" }
    }

    private func saveSplit() async {
        isSaving = true
        error = nil

        let request = CreateSplitRequest(
            name: splitName,
            rotationDays: rotationDays,
            exercises: exercises.map { ex in
                CreateExerciseBody(
                    dayName: ex.dayName,
                    exerciseName: ex.name,
                    targetSets: ex.sets,
                    targetReps: ex.reps,
                    currentWeight: ex.weight,
                    weightUnit: "lbs",
                    weightIncrement: ex.increment
                )
            }
        )

        do {
            let _: [String: String] = try await api.request(.createSplit(request))
            dismiss()
        } catch {
            self.error = "Failed to save: \(error.localizedDescription)"
        }

        isSaving = false
    }
}

struct ExerciseEntry: Identifiable {
    let id = UUID()
    var dayName: String
    var name: String = ""
    var sets: Int = 3
    var reps: Int = 10
    var weight: Double = 0
    var increment: Double = 2.5
}
