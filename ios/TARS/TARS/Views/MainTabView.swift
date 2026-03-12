import SwiftUI

struct MainTabView: View {
    @State private var selectedTab: Tab = .chat

    enum Tab: String {
        case chat, briefing, schedule, workout, jobs, settings
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            ChatView()
                .tabItem {
                    Label("Chat", systemImage: "bubble.left.and.bubble.right")
                }
                .tag(Tab.chat)

            BriefingView()
                .tabItem {
                    Label("Briefing", systemImage: "sun.max")
                }
                .tag(Tab.briefing)

            ScheduleView()
                .tabItem {
                    Label("Schedule", systemImage: "calendar")
                }
                .tag(Tab.schedule)

            WorkoutSessionView()
                .tabItem {
                    Label("Workout", systemImage: "figure.strengthtraining.traditional")
                }
                .tag(Tab.workout)

            JobsListView()
                .tabItem {
                    Label("Jobs", systemImage: "briefcase")
                }
                .tag(Tab.jobs)

            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
                .tag(Tab.settings)
        }
    }
}

#Preview {
    MainTabView()
}
