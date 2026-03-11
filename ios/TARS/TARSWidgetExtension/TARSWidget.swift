import SwiftUI
import WidgetKit

// MARK: - Widget Bundle (entry point for the extension)

@main
struct TARSWidgetBundle: WidgetBundle {
    var body: some Widget {
        ScheduleWidget()
        ApprovalsWidget()
        HealthWidget()
        OutfitWidget()
    }
}
