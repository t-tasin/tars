import SwiftUI
import WatchConnectivity

@main
struct TARSWatchApp: App {
    @StateObject private var connectivity = WatchConnectivityManager.shared

    var body: some Scene {
        WindowGroup {
            WatchMainView()
                .environmentObject(connectivity)
        }
    }
}
