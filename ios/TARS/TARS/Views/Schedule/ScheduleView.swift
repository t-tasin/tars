import SwiftUI

struct ScheduleView: View {
    @StateObject private var viewModel = ScheduleViewModel()

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Date picker strip
                DatePicker(
                    "Date",
                    selection: $viewModel.selectedDate,
                    displayedComponents: .date
                )
                .datePickerStyle(.compact)
                .padding(.horizontal)
                .padding(.vertical, 8)
                .onChange(of: viewModel.selectedDate) { _, newDate in
                    viewModel.selectDate(newDate)
                }

                Divider()

                // Leave-home-by banner
                if let leaveBy = viewModel.leaveHomeBy {
                    HStack(spacing: 8) {
                        Image(systemName: "car.fill")
                            .foregroundStyle(.orange)

                        Text("Leave by \(leaveBy)")
                            .font(.subheadline.weight(.medium))

                        if let note = viewModel.commuteNote {
                            Text("· \(note)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        Spacer()
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 10)
                    .background(.orange.opacity(0.08))

                    Divider()
                }

                // Content
                if viewModel.isLoading && viewModel.events.isEmpty {
                    Spacer()
                    ProgressView("Loading schedule...")
                    Spacer()
                } else if let error = viewModel.error, viewModel.events.isEmpty {
                    Spacer()
                    ContentUnavailableView {
                        Label("Failed to Load", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(error)
                    } actions: {
                        Button("Retry") { viewModel.fetchSchedule() }
                            .buttonStyle(.bordered)
                    }
                    Spacer()
                } else if viewModel.events.isEmpty {
                    Spacer()
                    ContentUnavailableView(
                        "No Events",
                        systemImage: "calendar",
                        description: Text("Nothing scheduled for \(viewModel.dateTitle).")
                    )
                    Spacer()
                } else {
                    timelineView
                }
            }
            .navigationTitle("Schedule")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if !viewModel.isToday {
                        Button("Today") { viewModel.goToToday() }
                            .font(.subheadline.weight(.medium))
                    }
                }
            }
            .task {
                viewModel.fetchSchedule()
            }
        }
    }

    // MARK: - Timeline

    private var timelineView: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(viewModel.events) { event in
                    eventRow(event)
                }
            }
            .padding(.vertical, 8)
        }
    }

    private func eventRow(_ event: CalendarEvent) -> some View {
        let colorInfo = viewModel.calendarColor(for: event)

        return HStack(alignment: .top, spacing: 12) {
            // Time column
            VStack(alignment: .trailing, spacing: 2) {
                if event.allDay {
                    Text("ALL DAY")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                } else {
                    Text(event.start, format: .dateTime.hour().minute())
                        .font(.caption.weight(.medium).monospacedDigit())
                    Text(event.end, format: .dateTime.hour().minute())
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.tertiary)
                }
            }
            .frame(width: 60, alignment: .trailing)

            // Color bar
            RoundedRectangle(cornerRadius: 2)
                .fill(Color(hue: colorInfo.hue, saturation: 0.6, brightness: 0.85))
                .frame(width: 4)
                .frame(minHeight: 44)

            // Event details
            VStack(alignment: .leading, spacing: 4) {
                Text(event.title)
                    .font(.subheadline.weight(.medium))

                HStack(spacing: 12) {
                    Text(viewModel.timeRange(for: event))
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if let location = event.location, !location.isEmpty {
                        Label(location, systemImage: "mappin")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }

                Text(event.calendar)
                    .font(.caption2)
                    .foregroundStyle(Color(hue: colorInfo.hue, saturation: 0.6, brightness: 0.85))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(
                        Color(hue: colorInfo.hue, saturation: 0.15, brightness: 0.95)
                    )
                    .clipShape(Capsule())
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
    }
}

#Preview {
    ScheduleView()
}
