import AppKit
import Security

struct ModelConfiguration: Codable {
    let apiKey: String
    let baseURL: String
    let model: String

    private enum CodingKeys: String, CodingKey {
        case apiKey = "api_key"
        case baseURL = "base_url"
        case model
    }

    var requestFields: [String: String] {
        ["api_key": apiKey, "base_url": baseURL, "model": model]
    }

    func validated() throws -> ModelConfiguration {
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let address = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty, key.unicodeScalars.allSatisfy({ (33...126).contains($0.value) }) else {
            throw ModelSettingsError("Enter a valid API key without spaces.")
        }
        let invalidURLCharacters = CharacterSet.whitespacesAndNewlines.union(.controlCharacters)
        guard address.rangeOfCharacter(from: invalidURLCharacters) == nil, !address.contains("\\"),
              let components = URLComponents(string: address),
              components.scheme?.lowercased() == "https", let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil, components.fragment == nil,
              components.port.map({ (1...65535).contains($0) }) != false,
              let url = components.url else {
            throw ModelSettingsError("Enter a valid HTTPS Base URL, such as https://api.openai.com/v1.")
        }
        guard !name.isEmpty, name.rangeOfCharacter(from: .controlCharacters) == nil else {
            throw ModelSettingsError("Enter the model ID from your provider.")
        }
        return ModelConfiguration(apiKey: key, baseURL: url.absoluteString, model: name)
    }
}

private struct ModelSettingsError: LocalizedError {
    let errorDescription: String?

    init(_ message: String) {
        errorDescription = message
    }
}

struct ModelSettingsStore {
    private let service = (Bundle.main.bundleIdentifier ?? "local.lightworld.app") + ".model-settings"

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: "default"]
    }

    func load() throws -> ModelConfiguration? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw ModelSettingsError("Could not read model settings from Keychain. Unlock it and try again (error \(status)).")
        }
        guard let config = try? JSONDecoder().decode(ModelConfiguration.self, from: data) else {
            throw ModelSettingsError("The saved model settings could not be read. Enter them again.")
        }
        return try config.validated()
    }

    func save(_ config: ModelConfiguration) throws {
        let data = try JSONEncoder().encode(config)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrLabel as String: "LightWorld Model Settings"
        ]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let entry = query.merging(attributes) { _, value in value }
            status = SecItemAdd(entry as CFDictionary, nil)
            if status == errSecDuplicateItem {
                status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
            }
        }
        guard status == errSecSuccess else {
            throw ModelSettingsError("Could not save model settings to Keychain. Unlock it and try again (error \(status)).")
        }
    }
}

private final class SettingsForm: NSView {
    override var isFlipped: Bool { true }
}

final class ModelSettingsView: NSView {
    private let card = NSView()
    private let apiKeyField = NSSecureTextField()
    private let baseURLField = NSTextField()
    private let modelField = NSTextField()
    private let statusLabel = NSTextField(wrappingLabelWithString: "")
    private var statusRow: NSGridRow?
    private let saveButton = NSButton(title: "Save Settings", target: nil, action: nil)
    private let closeButton = NSButton()
    private let store: ModelSettingsStore
    private let onFinish: (ModelConfiguration?) -> Void
    private var isSaving = false

    init(configuration: ModelConfiguration?, loadError: String?, store: ModelSettingsStore,
         onFinish: @escaping (ModelConfiguration?) -> Void) {
        self.store = store
        self.onFinish = onFinish
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.withAlphaComponent(0.35).cgColor
        card.wantsLayer = true
        card.layer?.cornerRadius = 16
        card.layer?.masksToBounds = true
        card.layer?.backgroundColor = NSColor(srgbRed: 0.105, green: 0.120, blue: 0.102, alpha: 1).cgColor
        card.layer?.borderColor = NSColor.white.withAlphaComponent(0.16).cgColor
        card.layer?.borderWidth = 1
        card.setAccessibilityRole(.group)
        card.setAccessibilityLabel("Model Settings")
        addSubview(card)
        configureForm(configuration: configuration, loadError: loadError)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {}
    override func rightMouseDown(with event: NSEvent) {}
    override func scrollWheel(with event: NSEvent) {}

    func placeCard(at frame: CGRect) {
        if card.frame != frame { card.frame = frame }
    }

    func beginEditing() {
        guard let window else { return }
        let field = apiKeyField.stringValue.isEmpty ? apiKeyField : modelField
        window.initialFirstResponder = field
        window.defaultButtonCell = saveButton.cell as? NSButtonCell
        window.makeFirstResponder(field)
    }

    func clearKey() { apiKeyField.stringValue = "" }

    func showNavigationError() {
        setStatus("Could not restore navigation. Try closing Model Settings again.", error: true)
    }

    private func configureForm(configuration: ModelConfiguration?, loadError: String?) {
        apiKeyField.stringValue = configuration?.apiKey ?? ""
        baseURLField.stringValue = configuration?.baseURL ?? ""
        modelField.stringValue = configuration?.model ?? ""
        let fields = [apiKeyField, baseURLField, modelField]
        let names = ["API Key", "Base URL", "Model ID"]
        let grid = NSGridView(views: zip(names, fields).map { name, field -> [NSView] in
            field.setAccessibilityLabel(name)
            field.cell?.usesSingleLineMode = true
            field.cell?.isScrollable = true
            field.setContentHuggingPriority(.defaultLow, for: .horizontal)
            field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            return [NSTextField(labelWithString: name), field]
        })
        grid.columnSpacing = 12
        grid.rowSpacing = 10
        grid.column(at: 0).xPlacement = .trailing
        grid.column(at: 1).xPlacement = .fill
        let messageRow = grid.addRow(with: [statusLabel, NSGridCell.emptyContentView])
        messageRow.mergeCells(in: NSRange(location: 0, length: 2))
        grid.cell(for: statusLabel)?.xPlacement = .fill
        statusRow = messageRow

        let title = NSTextField(labelWithString: "Model Settings")
        title.font = .systemFont(ofSize: 18, weight: .semibold)
        statusLabel.maximumNumberOfLines = 0

        saveButton.target = self
        saveButton.action = #selector(saveSettings)
        saveButton.keyEquivalent = "\r"
        closeButton.title = configuration == nil ? "Skip" : "Cancel"
        closeButton.target = self
        closeButton.action = #selector(dismissSettings)
        closeButton.keyEquivalent = "\u{1b}"
        apiKeyField.nextKeyView = baseURLField
        baseURLField.nextKeyView = modelField
        modelField.nextKeyView = closeButton
        closeButton.nextKeyView = saveButton
        saveButton.nextKeyView = apiKeyField
        let actions = NSStackView(views: [closeButton, saveButton])
        actions.orientation = .horizontal
        actions.spacing = 8

        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.scrollerStyle = .overlay
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        let form = SettingsForm()
        form.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = form

        for view in [title, scroll, actions] {
            view.translatesAutoresizingMaskIntoConstraints = false
            card.addSubview(view)
        }
        grid.translatesAutoresizingMaskIntoConstraints = false
        form.addSubview(grid)
        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: card.topAnchor, constant: 17),
            title.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 18),
            title.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -18),
            scroll.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 12),
            scroll.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            scroll.bottomAnchor.constraint(equalTo: actions.topAnchor, constant: -10),
            actions.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            actions.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -14),
            form.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            form.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            form.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
            grid.topAnchor.constraint(equalTo: form.topAnchor),
            grid.leadingAnchor.constraint(equalTo: form.leadingAnchor),
            grid.trailingAnchor.constraint(equalTo: form.trailingAnchor),
            grid.bottomAnchor.constraint(equalTo: form.bottomAnchor)
        ])
        setStatus(loadError ?? "", error: loadError != nil)
    }

    @objc private func saveSettings() {
        guard !isSaving, let window, window.makeFirstResponder(nil) else { return }
        let configuration: ModelConfiguration
        do {
            configuration = try ModelConfiguration(apiKey: apiKeyField.stringValue,
                                                   baseURL: baseURLField.stringValue,
                                                   model: modelField.stringValue).validated()
        } catch {
            setStatus(error.localizedDescription, error: true)
            return
        }
        setSaving(true)
        setStatus("Saving to Keychain…")
        DispatchQueue.global(qos: .userInitiated).async { [store, weak self] in
            let result = Result { try store.save(configuration) }
            DispatchQueue.main.async {
                guard let self else { return }
                self.setSaving(false)
                switch result {
                case .success:
                    self.onFinish(configuration)
                case .failure(let error):
                    self.setStatus(error.localizedDescription, error: true)
                }
            }
        }
    }

    @objc private func dismissSettings() {
        guard !isSaving else { return }
        onFinish(nil)
    }

    private func setSaving(_ saving: Bool) {
        isSaving = saving
        for control in [apiKeyField, baseURLField, modelField, closeButton, saveButton] {
            control.isEnabled = !saving
        }
    }

    private func setStatus(_ message: String, error: Bool = false) {
        statusLabel.stringValue = message
        statusLabel.textColor = error ? .systemRed : .secondaryLabelColor
        statusRow?.isHidden = message.isEmpty
        if error {
            layoutSubtreeIfNeeded()
            statusLabel.scrollToVisible(statusLabel.bounds)
        }
    }
}
