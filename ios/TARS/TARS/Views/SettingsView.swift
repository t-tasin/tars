import SwiftUI

struct SettingsView: View {
    @State private var apiKey = ""
    @State private var serverURL = ""
    @State private var showingSaveConfirmation = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("Server URL", text: $serverURL)
                        .textContentType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)

                    SecureField("API Key", text: $apiKey)
                        .textContentType(.password)
                }

                Section("Account") {
                    LabeledContent("Device Token") {
                        Text(KeychainService.shared.retrieve(forKey: .deviceToken) ?? "Not registered")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }

                Section {
                    Button("Save") {
                        saveSettings()
                    }
                }

                Section("About") {
                    LabeledContent("Version", value: Bundle.main.appVersion)
                    LabeledContent("Build", value: Bundle.main.buildNumber)
                }
            }
            .navigationTitle("Settings")
            .alert("Settings Saved", isPresented: $showingSaveConfirmation) {
                Button("OK", role: .cancel) { }
            }
            .onAppear {
                apiKey = KeychainService.shared.retrieve(forKey: .apiKey) ?? ""
                serverURL = KeychainService.shared.retrieve(forKey: .serverURL) ?? ""
            }
        }
    }

    private func saveSettings() {
        if !apiKey.isEmpty {
            KeychainService.shared.save(apiKey, forKey: .apiKey)
        }
        if !serverURL.isEmpty {
            KeychainService.shared.save(serverURL, forKey: .serverURL)
        }
        showingSaveConfirmation = true
    }
}

extension Bundle {
    var appVersion: String {
        infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
    }

    var buildNumber: String {
        infoDictionary?["CFBundleVersion"] as? String ?? "1"
    }
}

#Preview {
    SettingsView()
}
