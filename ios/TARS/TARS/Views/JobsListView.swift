import SwiftUI

struct JobsListView: View {
    @State private var jobs: [JobListing] = []

    var body: some View {
        NavigationStack {
            Group {
                if jobs.isEmpty {
                    ContentUnavailableView(
                        "No Jobs",
                        systemImage: "briefcase",
                        description: Text("Job listings will appear here.")
                    )
                } else {
                    List(jobs) { job in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(job.title)
                                .font(.headline)
                            Text(job.company)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                            HStack {
                                if let location = job.location {
                                    Label(location, systemImage: "mappin")
                                }
                                if let salary = job.salaryRange {
                                    Label(salary, systemImage: "dollarsign")
                                }
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)

                            if let score = job.matchScore {
                                ProgressView(value: Double(score), total: 100)
                                    .tint(score >= 70 ? .green : score >= 40 ? .orange : .red)
                            }

                            HStack {
                                Text(job.status.rawValue.capitalized)
                                    .font(.caption2)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 2)
                                    .background(.ultraThinMaterial)
                                    .clipShape(Capsule())
                                Spacer()
                                Text(job.source)
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            .navigationTitle("Jobs")
        }
    }
}

#Preview {
    JobsListView()
}
