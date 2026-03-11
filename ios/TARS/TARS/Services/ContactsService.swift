import Foundation
import Contacts

// MARK: - Sync models

struct ContactSyncEntry: Codable, Sendable {
    let appleContactId: String
    let fullName: String
    let emailAddresses: [String]
    let phoneNumbers: [String]
    let organization: String?
    let jobTitle: String?
    let relationship: String?
}

struct ContactsSyncRequest: Codable, Sendable {
    let contacts: [ContactSyncEntry]
    let fullSync: Bool
}

struct ContactsSyncResponse: Codable, Sendable {
    let upserted: Int
    let removed: Int
    let total: Int
}

// MARK: - ContactsService

@MainActor
final class ContactsService: ObservableObject {
    static let shared = ContactsService()

    @Published var isAuthorized = false
    @Published var isSyncing = false
    @Published var lastSyncDate: Date?
    @Published var lastSyncCount = 0
    @Published var error: String?

    private let store = CNContactStore()
    private let api = APIClient.shared

    /// Keys we need from each contact.
    private let keysToFetch: [CNKeyDescriptor] = [
        CNContactIdentifierKey as CNKeyDescriptor,
        CNContactGivenNameKey as CNKeyDescriptor,
        CNContactFamilyNameKey as CNKeyDescriptor,
        CNContactMiddleNameKey as CNKeyDescriptor,
        CNContactEmailAddressesKey as CNKeyDescriptor,
        CNContactPhoneNumbersKey as CNKeyDescriptor,
        CNContactOrganizationNameKey as CNKeyDescriptor,
        CNContactJobTitleKey as CNKeyDescriptor,
        CNContactRelationsKey as CNKeyDescriptor,
    ]

    private init() {
        checkAuthorizationStatus()
    }

    // MARK: - Authorization

    private func checkAuthorizationStatus() {
        let status = CNContactStore.authorizationStatus(for: .contacts)
        isAuthorized = status == .authorized
    }

    func requestAccess() async {
        do {
            let granted = try await store.requestAccess(for: .contacts)
            isAuthorized = granted
            if !granted {
                error = "Contacts access denied"
            }
        } catch {
            self.error = "Contacts access failed: \(error.localizedDescription)"
        }
    }

    // MARK: - Sync

    /// Full sync: fetch all contacts and send to backend.
    func syncContacts(fullSync: Bool = true) async {
        guard !isSyncing else { return }
        guard isAuthorized else {
            await requestAccess()
            guard isAuthorized else { return }
        }

        isSyncing = true
        error = nil

        do {
            let entries = try fetchAllContacts()

            guard !entries.isEmpty else {
                isSyncing = false
                return
            }

            let request = ContactsSyncRequest(contacts: entries, fullSync: fullSync)
            let _: ContactsSyncResponse = try await api.request(
                .contactsSync(request)
            )

            lastSyncDate = Date()
            lastSyncCount = entries.count
        } catch {
            self.error = "Contacts sync failed: \(error.localizedDescription)"
        }

        isSyncing = false
    }

    // MARK: - Fetch contacts

    private func fetchAllContacts() throws -> [ContactSyncEntry] {
        var entries: [ContactSyncEntry] = []
        let request = CNContactFetchRequest(keysToFetch: keysToFetch)
        request.sortOrder = .givenName

        try store.enumerateContacts(with: request) { contact, _ in
            let fullName = [contact.givenName, contact.middleName, contact.familyName]
                .filter { !$0.isEmpty }
                .joined(separator: " ")

            // Skip contacts with no name and no email
            guard !fullName.isEmpty || !contact.emailAddresses.isEmpty else { return }

            let emails = contact.emailAddresses.map { String($0.value) }
            let phones = contact.phoneNumbers.map { $0.value.stringValue }
            let org = contact.organizationName.isEmpty ? nil : contact.organizationName
            let title = contact.jobTitle.isEmpty ? nil : contact.jobTitle

            // Use first relationship label if available
            let relationship = contact.contactRelations.first.map { rel in
                rel.label.map { CNLabeledValue<NSString>.localizedString(forLabel: $0) } ?? "other"
            }

            entries.append(ContactSyncEntry(
                appleContactId: contact.identifier,
                fullName: fullName,
                emailAddresses: emails,
                phoneNumbers: phones,
                organization: org,
                jobTitle: title,
                relationship: relationship
            ))
        }

        return entries
    }
}

// MARK: - Endpoint extension

extension Endpoint {
    static func contactsSync(_ request: ContactsSyncRequest) -> Endpoint {
        .custom(path: "/api/v1/contacts/sync", method: "POST", body: request)
    }
}
