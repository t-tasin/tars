import SwiftUI

struct BriefingSectionView: View {
    let section: BriefingSection
    let content: [String]
    let isExpanded: Bool
    let onToggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            Button(action: onToggle) {
                HStack(spacing: 12) {
                    Image(systemName: section.icon)
                        .font(.title3)
                        .foregroundStyle(sectionColor)
                        .frame(width: 28)

                    Text(section.title)
                        .font(.headline)
                        .foregroundStyle(.primary)

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                }
                .padding(.vertical, 12)
                .padding(.horizontal, 16)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            // Content
            if isExpanded {
                VStack(alignment: .leading, spacing: 6) {
                    if content.isEmpty {
                        Text("No data available")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(Array(content.enumerated()), id: \.offset) { _, line in
                            HStack(alignment: .top, spacing: 8) {
                                Circle()
                                    .fill(sectionColor.opacity(0.5))
                                    .frame(width: 5, height: 5)
                                    .padding(.top, 6)

                                Text(line)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .padding(.horizontal, 56)
                .padding(.bottom, 12)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(Color(.systemGray6))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private var sectionColor: Color {
        switch section.tintColor {
        case "blue":    return .blue
        case "orange":  return .orange
        case "indigo":  return .indigo
        case "red":     return .red
        case "green":   return .green
        case "purple":  return .purple
        default:        return .blue
        }
    }
}

#Preview {
    VStack(spacing: 12) {
        BriefingSectionView(
            section: .weather,
            content: ["72°F, Partly Cloudy", "High: 78° / Low: 62°"],
            isExpanded: true,
            onToggle: {}
        )
        BriefingSectionView(
            section: .email,
            content: ["Prof. Sadigh: Research update", "GitHub: PR review requested"],
            isExpanded: false,
            onToggle: {}
        )
    }
    .padding()
}
