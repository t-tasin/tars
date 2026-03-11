import Foundation
import HealthKit

// MARK: - Sync models

struct HealthDataPoint: Codable, Sendable {
    let type: String
    let value: Double
    let unit: String
    let date: String
    let metadata: [String: String]

    init(type: String, value: Double, unit: String, date: Date, metadata: [String: String] = [:]) {
        self.type = type
        self.value = value
        self.unit = unit
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        self.date = formatter.string(from: date)
        self.metadata = metadata
    }
}

struct HealthDataSyncRequest: Codable, Sendable {
    let data: [HealthDataPoint]
}

struct HealthDataSyncResponse: Codable, Sendable {
    let synced: Int
    let total: Int
}

// MARK: - HealthKitService

@MainActor
final class HealthKitService: ObservableObject {
    static let shared = HealthKitService()

    @Published var isAuthorized = false
    @Published var isSyncing = false
    @Published var lastSyncDate: Date?
    @Published var error: String?

    private let store = HKHealthStore()
    private let api = APIClient.shared

    // Types we read from HealthKit
    private let readTypes: Set<HKObjectType> = {
        var types: Set<HKObjectType> = []
        if let sleep = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) {
            types.insert(sleep)
        }
        if let steps = HKObjectType.quantityType(forIdentifier: .stepCount) {
            types.insert(steps)
        }
        if let heartRate = HKObjectType.quantityType(forIdentifier: .heartRate) {
            types.insert(heartRate)
        }
        if let exerciseMinutes = HKObjectType.quantityType(forIdentifier: .appleExerciseTime) {
            types.insert(exerciseMinutes)
        }
        types.insert(HKObjectType.workoutType())
        return types
    }()

    private init() {}

    // MARK: - Authorization

    var isAvailable: Bool {
        HKHealthStore.isHealthDataAvailable()
    }

    func requestAuthorization() async {
        guard isAvailable else {
            error = "HealthKit is not available on this device"
            return
        }

        do {
            try await store.requestAuthorization(toShare: [], read: readTypes)
            isAuthorized = true
            error = nil
        } catch {
            self.error = "HealthKit authorization failed: \(error.localizedDescription)"
        }
    }

    // MARK: - Sync

    /// Fetch yesterday's health data and sync to backend.
    func syncYesterdayData() async {
        guard isAuthorized else {
            await requestAuthorization()
            guard isAuthorized else { return }
        }
        guard !isSyncing else { return }

        isSyncing = true
        error = nil

        do {
            let calendar = Calendar.current
            let endOfYesterday = calendar.startOfDay(for: Date())
            let startOfYesterday = calendar.date(byAdding: .day, value: -1, to: endOfYesterday)!

            async let stepsData = fetchSteps(start: startOfYesterday, end: endOfYesterday)
            async let sleepData = fetchSleep(start: startOfYesterday, end: endOfYesterday)
            async let heartRateData = fetchHeartRate(start: startOfYesterday, end: endOfYesterday)
            async let exerciseData = fetchExerciseMinutes(start: startOfYesterday, end: endOfYesterday)
            async let workoutData = fetchWorkouts(start: startOfYesterday, end: endOfYesterday)

            var points: [HealthDataPoint] = []
            if let steps = try await stepsData { points.append(steps) }
            if let sleep = try await sleepData { points.append(sleep) }
            if let hr = try await heartRateData { points.append(hr) }
            if let exercise = try await exerciseData { points.append(exercise) }
            points.append(contentsOf: try await workoutData)

            guard !points.isEmpty else {
                isSyncing = false
                return
            }

            let _: HealthDataSyncResponse = try await api.request(
                .healthDataSync(HealthDataSyncRequest(data: points))
            )
            lastSyncDate = Date()
        } catch {
            self.error = "Health sync failed: \(error.localizedDescription)"
        }

        isSyncing = false
    }

    // MARK: - Queries

    private func fetchSteps(start: Date, end: Date) async throws -> HealthDataPoint? {
        guard let type = HKQuantityType.quantityType(forIdentifier: .stepCount) else { return nil }
        let sum = try await statisticsSum(type: type, start: start, end: end, unit: .count())
        guard let sum else { return nil }
        return HealthDataPoint(type: "steps", value: sum, unit: "count", date: start)
    }

    private func fetchSleep(start: Date, end: Date) async throws -> HealthDataPoint? {
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else { return nil }

        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictEndDate)
        let samples = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<[HKCategorySample], Error>) in
            let query = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, results, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: results as? [HKCategorySample] ?? [])
                }
            }
            store.execute(query)
        }

        // Sum asleep durations (InBed, Asleep, REM, Core, Deep)
        let asleepValues: Set<Int> = [
            HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue,
            HKCategoryValueSleepAnalysis.asleepREM.rawValue,
            HKCategoryValueSleepAnalysis.asleepCore.rawValue,
            HKCategoryValueSleepAnalysis.asleepDeep.rawValue,
        ]

        let totalMinutes = samples
            .filter { asleepValues.contains($0.value) }
            .reduce(0.0) { sum, sample in
                sum + sample.endDate.timeIntervalSince(sample.startDate) / 60.0
            }

        guard totalMinutes > 0 else { return nil }
        let hours = (totalMinutes / 60.0 * 10).rounded() / 10
        return HealthDataPoint(type: "sleep", value: hours, unit: "hours", date: start)
    }

    private func fetchHeartRate(start: Date, end: Date) async throws -> HealthDataPoint? {
        guard let type = HKQuantityType.quantityType(forIdentifier: .heartRate) else { return nil }
        let avg = try await statisticsAvg(type: type, start: start, end: end, unit: HKUnit.count().unitDivided(by: .minute()))
        guard let avg else { return nil }
        return HealthDataPoint(type: "heart_rate_avg", value: (avg * 10).rounded() / 10, unit: "bpm", date: start)
    }

    private func fetchExerciseMinutes(start: Date, end: Date) async throws -> HealthDataPoint? {
        guard let type = HKQuantityType.quantityType(forIdentifier: .appleExerciseTime) else { return nil }
        let sum = try await statisticsSum(type: type, start: start, end: end, unit: .minute())
        guard let sum else { return nil }
        return HealthDataPoint(type: "exercise_minutes", value: sum, unit: "minutes", date: start)
    }

    private func fetchWorkouts(start: Date, end: Date) async throws -> [HealthDataPoint] {
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictEndDate)
        let samples = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<[HKWorkout], Error>) in
            let query = HKSampleQuery(sampleType: .workoutType(), predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, results, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: results as? [HKWorkout] ?? [])
                }
            }
            store.execute(query)
        }

        return samples.map { workout in
            let durationMin = (workout.duration / 60.0 * 10).rounded() / 10
            let calories = workout.totalEnergyBurned?.doubleValue(for: .kilocalorie()) ?? 0
            let activityName = workout.workoutActivityType.displayName

            return HealthDataPoint(
                type: "workout",
                value: durationMin,
                unit: "minutes",
                date: workout.startDate,
                metadata: [
                    "activity": activityName,
                    "calories": "\(Int(calories))",
                ]
            )
        }
    }

    // MARK: - Statistics helpers

    private func statisticsSum(type: HKQuantityType, start: Date, end: Date, unit: HKUnit) async throws -> Double? {
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictEndDate)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: .cumulativeSum) { _, stats, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: stats?.sumQuantity()?.doubleValue(for: unit))
                }
            }
            store.execute(query)
        }
    }

    private func statisticsAvg(type: HKQuantityType, start: Date, end: Date, unit: HKUnit) async throws -> Double? {
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictEndDate)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: .discreteAverage) { _, stats, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: stats?.averageQuantity()?.doubleValue(for: unit))
                }
            }
            store.execute(query)
        }
    }
}

// MARK: - Workout activity name helper

private extension HKWorkoutActivityType {
    var displayName: String {
        switch self {
        case .running:                  return "Running"
        case .walking:                  return "Walking"
        case .cycling:                  return "Cycling"
        case .swimming:                 return "Swimming"
        case .functionalStrengthTraining: return "Strength Training"
        case .traditionalStrengthTraining: return "Strength Training"
        case .yoga:                     return "Yoga"
        case .highIntensityIntervalTraining: return "HIIT"
        case .elliptical:               return "Elliptical"
        case .rowing:                   return "Rowing"
        case .stairClimbing:            return "Stair Climbing"
        case .hiking:                   return "Hiking"
        case .dance:                    return "Dance"
        case .cooldown:                 return "Cooldown"
        case .coreTraining:             return "Core Training"
        case .pilates:                  return "Pilates"
        default:                        return "Workout"
        }
    }
}

// MARK: - Endpoint convenience

extension Endpoint {
    static func healthDataSync(_ body: HealthDataSyncRequest) -> Endpoint {
        .custom(path: "/api/v1/health-data/sync", method: "POST", body: body)
    }
}
