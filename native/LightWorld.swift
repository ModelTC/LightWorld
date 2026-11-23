import AppKit
import CoreGraphics
import Darwin
import UniformTypeIdentifiers

final class PromptPanel: NSPanel {
    var allowsKey = false

    override var canBecomeKey: Bool { allowsKey }
    override var canBecomeMain: Bool { allowsKey }
}

final class PromptEditor: NSTextView {
    var onFocus: (() -> Void)?
    var placeholder: NSAttributedString? {
        didSet { needsDisplay = true }
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func becomeFirstResponder() -> Bool {
        guard super.becomeFirstResponder() else { return false }
        onFocus?()
        return true
    }

    override func mouseDown(with event: NSEvent) {
        guard isEditable else { return }
        focusForEditing()
        super.mouseDown(with: event)
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        if string.isEmpty {
            placeholder?.draw(in: bounds.insetBy(dx: textContainerInset.width, dy: textContainerInset.height))
        }
    }

    override func setAccessibilityFocused(_ focused: Bool) {
        super.setAccessibilityFocused(focused)
        if focused { focusForEditing() }
    }

    func focusForEditing() {
        guard isEditable, let window else { return }
        if !window.isKeyWindow { window.makeKeyAndOrderFront(nil) }
        if window.firstResponder !== self { window.makeFirstResponder(self) }
        onFocus?()
    }
}

private final class PromptScrollView: NSScrollView {
    private(set) var textHeight: CGFloat = 0
    var onHeightChange: (() -> Void)?
    private var measuring = false

    override func layout() {
        super.layout()
        measureText()
    }

    func measureText() {
        guard !measuring, contentSize.width > 0, let editor = documentView as? NSTextView,
              let container = editor.textContainer, let manager = editor.layoutManager else { return }
        measuring = true
        defer { measuring = false }

        if abs(editor.frame.width - contentSize.width) > 0.5 {
            editor.setFrameSize(NSSize(width: contentSize.width, height: editor.frame.height))
        }
        manager.ensureLayout(for: container)
        // The empty final line also needs room after Shift+Return.
        let textBottom = max(manager.usedRect(for: container).maxY, manager.extraLineFragmentRect.maxY)
        let height = ceil(textBottom + 2 * editor.textContainerInset.height)
        guard height != textHeight else { return }
        textHeight = height
        onHeightChange?()
    }

    func updateScrolling(viewportHeight: CGFloat) {
        let overflowing = textHeight > viewportHeight
        if hasVerticalScroller != overflowing {
            hasVerticalScroller = overflowing
            verticalScrollElasticity = overflowing ? .automatic : .none
        }
        if !overflowing && contentView.bounds.origin != .zero {
            contentView.scroll(to: .zero)
            reflectScrolledClipView(contentView)
        }
    }
}

final class PromptBox: NSView {
    var onClick: (() -> Void)?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { onClick?() }
}

final class AccessoryPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class OverlayButton: NSButton {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

private enum UpdateState: Equatable {
    case walking, pausing, designing, loading, resuming
    case blocked(canRetry: Bool)

    var allowsUpdate: Bool { self == .walking || self == .blocked(canRetry: true) }

    var isBlocked: Bool {
        if case .blocked = self { return true }
        return false
    }
}

private enum WorldChange {
    case design(prompt: String, configuration: ModelConfiguration)
    case open(URL)
}

private struct UpdateRequest {
    let id = UUID().uuidString
    let change: WorldChange
    let wasWalking: Bool
    var pose: PlayerSnapshot?
}

private enum InputMode {
    case roaming, typing, paused, configurationRequired
}

private struct PlayerSnapshot: Decodable {
    let pid: Int32
    let blendPath: String
    let status: String
    let paused: Bool?
    let position: [Double]?
    let yaw: Double?
    let thirdPerson: Bool
    let canvasClickTypingSession: String?
    let targetBlendPath: String?
    let message: String?

    enum CodingKeys: String, CodingKey {
        case pid, status, paused, position, yaw, message
        case blendPath = "blend_path"
        case thirdPerson = "third_person"
        case canvasClickTypingSession = "canvas_click_typing_session"
        case targetBlendPath = "target_blend_path"
    }
}

private struct CanvasWindow {
    let frame: CGRect
    let windowFrame: CGRect
    let number: Int
    let windowsInFront: [Int]
}

final class LightWorld: NSObject, NSApplicationDelegate, NSWindowDelegate, NSTextViewDelegate, NSMenuItemValidation {
    private let state: URL
    private let resources: URL
    private let playerCache: URL
    private let bundledPython: URL?
    private let upbge: URL
    private let blenderResources: URL
    private let panel = PromptPanel(contentRect: NSRect(x: 0, y: 0, width: 600, height: OverlayLayout.promptHeight),
                                    styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
    private let toolbarPanel = AccessoryPanel(
        contentRect: NSRect(x: 0, y: 0, width: OverlayLayout.toolbarWidth, height: OverlayLayout.toolbarHeight),
        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false
    )
    private let logPanel = AccessoryPanel(contentRect: NSRect(x: 0, y: 0, width: OverlayLayout.logWidth, height: 237),
                                         styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
    private let prompt = PromptEditor()
    private let promptScroll = PromptScrollView()
    private let promptBox = PromptBox()
    private let toolbarSurface = NSView()
    private let logSurface = NSView()
    private let modeIcon = NSImageView()
    private let sendButton = OverlayButton()
    private let logDot = NSView()
    private let logChevron = NSImageView()
    private let worldName = NSTextField(labelWithString: "Untitled World")
    private let viewButton = OverlayButton()
    private let openWorldButton = OverlayButton()
    private let saveWorldButton = OverlayButton()
    private let modelSettingsButton = OverlayButton()
    private let toggleLogButton = OverlayButton()
    private let blendType = UTType(filenameExtension: "blend") ?? .data
    private let logScroll = NSScrollView()
    private let logView = NSTextView()
    private let clock: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "HH:mm:ss"
        return formatter
    }()
    private var logExpanded = false
    private var logBodyConstraint: NSLayoutConstraint?
    private var filePicker: NSSavePanel?
    private var typingSession: String?
    private var viewToggleCount = 0
    private let serviceInput = Pipe()
    private let serviceOutput = Pipe()
    private var service: Process?
    private var player: Process?
    private var loadingPanel: AccessoryPanel?
    private var stdoutBuffer = Data()
    private var timer: Timer?
    private var displayedCanvas: CanvasWindow?
    private var canvasMouseMonitor: Any?
    private var canvasDragTimer: Timer?
    private var canvasLastMove: TimeInterval = 0
    private var serviceLog: FileHandle?
    private var playerLog: FileHandle?
    private var pendingScenePath: String?
    private var sceneDeadline = Date.distantFuture
    private var launchDeadline = Date.distantFuture
    private let modelSettingsStore = ModelSettingsStore()
    private var modelSettingsLoaded = false
    private var modelSettingsLoadError: String?
    private var modelConfiguration: ModelConfiguration?
    private var modelSettingsView: ModelSettingsView?
    private var initialPlayer: (scene: String, executable: URL)?
    private var updateState = UpdateState.walking
    private var updateRequest: UpdateRequest?
    private var activeScenePath: String?
    private var closing = false
    private var pendingRecoveryError: String?
    private var worldFile: URL?
    private var saveRequestID: String?
    private let healthy = NSColor(srgbRed: 0.70, green: 0.77, blue: 0.63, alpha: 1)
    private let working = NSColor(srgbRed: 0.83, green: 0.70, blue: 0.52, alpha: 1)
    private let failed = NSColor(srgbRed: 0.92, green: 0.65, blue: 0.58, alpha: 1)
    private let editingTint = NSColor(srgbRed: 0.89, green: 0.85, blue: 0.74, alpha: 1)

    private var hasScene: Bool { activeScenePath != nil }

    private var accessories: [(panel: NSPanel, surface: NSView)] {
        [(toolbarPanel, toolbarSurface), (logPanel, logSurface)]
    }

    override init() {
        let environment = ProcessInfo.processInfo.environment
        let directory = Bundle.main.bundleURL.deletingLastPathComponent()
        let files = FileManager.default
        let cache = files.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("LightWorld", isDirectory: true)
        let customState = environment["LIGHTWORLD_STATE_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
        state = customState ?? cache.appendingPathComponent(UUID().uuidString, isDirectory: true)
        playerCache = (customState ?? cache).appendingPathComponent("Player", isDirectory: true)
        resources = Bundle.main.resourceURL ?? directory
        bundledPython = (Bundle.main.object(forInfoDictionaryKey: "LightWorldPythonExecutable") as? String)
            .map { Bundle.main.bundleURL.appendingPathComponent($0) }
        if bundledPython != nil {
            upbge = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers", isDirectory: true)
        } else {
            upbge = URL(fileURLWithPath: environment["LIGHTWORLD_UPBGE"] ?? "/Applications/UPBGE")
        }
        blenderResources = upbge.appendingPathComponent("Blender.app/Contents/Resources", isDirectory: true)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        configureMenu()
        configurePromptPanel()
        configureToolbar()
        configureLogPanel()
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(workspaceDidActivateApplication(_:)),
            name: NSWorkspace.didActivateApplicationNotification, object: nil
        )
        setStatus("Opening world…", tint: working)
        NSApp.activate(ignoringOtherApps: true)
        do {
            try FileManager.default.createDirectory(at: state, withIntermediateDirectories: true)
            try startService()
            loadModelSettings()
        } catch {
            showFatal("LightWorld could not start. Error \((error as NSError).code).")
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if modelSettingsView != nil {
            focusModelSettings()
        } else if let filePicker {
            filePicker.makeKeyAndOrderFront(nil)
        } else if hasScene {
            focusPlayer()
        }
        return true
    }

    func applicationDidResignActive(_ notification: Notification) {
        if !panel.isKeyWindow { endPromptInput() }
    }

    func windowDidResignKey(_ notification: Notification) {
        endPromptInput()
    }

    func textDidChange(_ notification: Notification) {
        promptDidChange()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let player, player.isRunning else { return .terminateNow }
        closing = true
        timer?.invalidate()
        stopTrackingCanvasDrag()
        dismissLoadingPanel()
        panel.orderOut(nil)
        toolbarPanel.orderOut(nil)
        logPanel.orderOut(nil)
        let playerPID = player.processIdentifier
        if let application = NSRunningApplication(processIdentifier: playerPID) {
            application.terminate()
        } else {
            player.terminate()
        }
        // Some UPBGE builds keep running after a quit request. Only stop the child we launched.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
            if player.isRunning { Darwin.kill(playerPID, SIGKILL) }
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        closing = true
        timer?.invalidate()
        stopTrackingCanvasDrag()
        if let canvasMouseMonitor { NSEvent.removeMonitor(canvasMouseMonitor) }
        serviceOutput.fileHandleForReading.readabilityHandler = nil
        try? serviceInput.fileHandleForWriting.close()
        if let service, service.isRunning { service.terminate() }
    }

    private func configureMenu() {
        let menu = NSMenu()
        let app = NSMenuItem()
        let commands = NSMenu()
        let settings = commands.addItem(withTitle: "Model Settings…", action: #selector(openModelSettings), keyEquivalent: ",")
        settings.target = self
        commands.addItem(.separator())
        commands.addItem(withTitle: "Quit LightWorld", action: #selector(NSApplication.terminate(_:)),
                         keyEquivalent: "q")
        app.submenu = commands
        menu.addItem(app)
        let fileItem = NSMenuItem()
        let fileMenu = NSMenu(title: "File")
        let open = fileMenu.addItem(withTitle: "Open World…", action: #selector(openWorld), keyEquivalent: "o")
        open.target = self
        let save = fileMenu.addItem(withTitle: "Save World…", action: #selector(saveWorld), keyEquivalent: "s")
        save.target = self
        fileMenu.addItem(.separator())
        let logItem = fileMenu.addItem(withTitle: "Toggle Design Log", action: #selector(toggleLog), keyEquivalent: "")
        logItem.target = self
        fileItem.submenu = fileMenu
        menu.addItem(fileItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: NSSelectorFromString("undo:"), keyEquivalent: "z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        menu.addItem(editItem)
        NSApp.mainMenu = menu
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        switch menuItem.action {
        case #selector(openModelSettings): return modelSettingsButton.isEnabled
        case #selector(openWorld): return openWorldButton.isEnabled
        case #selector(saveWorld): return saveWorldButton.isEnabled
        case #selector(toggleLog):
            menuItem.title = logScroll.isHidden ? "Show Log" : "Hide Log"
            return toggleLogButton.isEnabled && logPanel.isVisible
        default: return true
        }
    }

    // MARK: - Overlay windows

    private func configureOverlay(_ window: NSPanel, surface: NSView, title: String, radius: CGFloat = 15) {
        window.title = title
        window.backgroundColor = .clear
        window.isOpaque = false
        window.hasShadow = true
        window.hidesOnDeactivate = false
        window.level = .normal
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        window.appearance = NSAppearance(named: .darkAqua)
        window.animationBehavior = .none

        surface.wantsLayer = true
        surface.layer?.cornerRadius = radius
        surface.layer?.masksToBounds = true
        surface.layer?.backgroundColor = NSColor(srgbRed: 0.087, green: 0.099, blue: 0.085, alpha: 0.97).cgColor
        surface.layer?.borderWidth = 1
        surface.layer?.borderColor = editingTint.withAlphaComponent(0.16).cgColor
        window.contentView = surface
    }

    private func configurePromptPanel() {
        let surface = NSView()
        configureOverlay(panel, surface: surface, title: "LightWorld")
        surface.layer?.backgroundColor = NSColor.clear.cgColor
        surface.layer?.borderWidth = 0
        surface.layer?.cornerRadius = 0
        panel.delegate = self

        promptBox.wantsLayer = true
        promptBox.layer?.cornerRadius = 15
        promptBox.onClick = { [weak self] in self?.prompt.focusForEditing() }

        prompt.delegate = self
        prompt.onFocus = { [weak self] in self?.setPromptFocus(true) }
        prompt.drawsBackground = false
        prompt.focusRingType = .none
        let promptFont = NSFont.systemFont(ofSize: 16)
        prompt.font = promptFont
        prompt.textColor = NSColor(srgbRed: 0.96, green: 0.94, blue: 0.88, alpha: 1)
        prompt.insertionPointColor = editingTint
        prompt.setAccessibilityLabel("Scene design prompt")
        prompt.isEditable = false
        prompt.isSelectable = false
        prompt.isRichText = false
        prompt.isHorizontallyResizable = false
        prompt.isVerticallyResizable = true
        prompt.autoresizingMask = [.width]
        prompt.textContainerInset = NSSize(width: 0, height: 2)
        prompt.textContainer?.lineFragmentPadding = 0
        prompt.textContainer?.widthTracksTextView = true

        let editorHeight = OverlayLayout.promptEditorMinimumHeight
        prompt.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        prompt.minSize = NSSize(width: 0, height: editorHeight)
        prompt.frame.size = NSSize(width: 500, height: editorHeight)
        promptScroll.drawsBackground = false
        promptScroll.hasVerticalScroller = false
        promptScroll.autohidesScrollers = true
        promptScroll.scrollerStyle = .overlay
        promptScroll.verticalScrollElasticity = .none
        promptScroll.documentView = prompt
        promptScroll.onHeightChange = { [weak self] in
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.closing, let canvas = self.displayedCanvas else { return }
                self.positionOverlays(in: canvas.frame)
            }
        }

        sendButton.title = "Send"
        sendButton.setAccessibilityLabel("Send Prompt")
        sendButton.toolTip = "Send Prompt (Return)"
        sendButton.bezelStyle = .rounded
        sendButton.font = .systemFont(ofSize: 13, weight: .semibold)
        sendButton.target = self
        sendButton.action = #selector(submitPrompt)
        sendButton.isEnabled = false

        modeIcon.imageScaling = .scaleProportionallyDown
        modeIcon.setAccessibilityElement(false)
        let firstBaseline = prompt.layoutManager?.defaultBaselineOffset(for: promptFont) ?? promptFont.ascender
        let iconCenterFromTop = prompt.textContainerInset.height + firstBaseline - promptFont.capHeight / 2

        promptBox.frame = surface.bounds
        surface.addSubview(promptBox)
        for view in [modeIcon, promptScroll, sendButton] {
            view.translatesAutoresizingMaskIntoConstraints = false
            promptBox.addSubview(view)
        }
        NSLayoutConstraint.activate([
            modeIcon.leadingAnchor.constraint(equalTo: promptBox.leadingAnchor, constant: 17),
            modeIcon.centerYAnchor.constraint(equalTo: promptScroll.topAnchor, constant: iconCenterFromTop),
            modeIcon.widthAnchor.constraint(equalToConstant: 17),
            modeIcon.heightAnchor.constraint(equalToConstant: 17),
            promptScroll.leadingAnchor.constraint(equalTo: modeIcon.trailingAnchor, constant: 10),
            promptScroll.topAnchor.constraint(equalTo: promptBox.topAnchor, constant: OverlayLayout.promptVerticalPadding),
            promptScroll.bottomAnchor.constraint(equalTo: promptBox.bottomAnchor, constant: -OverlayLayout.promptVerticalPadding),
            promptScroll.trailingAnchor.constraint(equalTo: sendButton.leadingAnchor, constant: -8),
            sendButton.trailingAnchor.constraint(equalTo: promptBox.trailingAnchor, constant: -13),
            sendButton.centerYAnchor.constraint(equalTo: promptBox.centerYAnchor),
            sendButton.widthAnchor.constraint(equalToConstant: 60),
            sendButton.heightAnchor.constraint(equalToConstant: 30)
        ])
    }

    private func configureToolbar() {
        let surface = toolbarSurface
        configureOverlay(toolbarPanel, surface: surface, title: "World Toolbar", radius: 12)
        let icon = NSImageView()
        icon.image = NSImage(systemSymbolName: "leaf.fill", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 12, weight: .semibold))
        icon.contentTintColor = healthy
        icon.imageScaling = .scaleProportionallyDown
        icon.setAccessibilityElement(false)
        worldName.font = .systemFont(ofSize: 12, weight: .semibold)
        worldName.textColor = NSColor.white.withAlphaComponent(0.89)
        worldName.lineBreakMode = .byTruncatingMiddle
        worldName.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        configureWorldButton(viewButton, title: "", label: "", symbol: "person", action: #selector(toggleView))
        updateViewButton(thirdPerson: true)
        configureWorldButton(openWorldButton, title: "Open", label: "Open World",
                             symbol: "folder", action: #selector(openWorld))
        configureWorldButton(saveWorldButton, title: "Save", label: "Save World",
                             symbol: "square.and.arrow.down", action: #selector(saveWorld))
        configureWorldButton(modelSettingsButton, title: "", label: "Model Settings",
                             symbol: "gearshape", action: #selector(openModelSettings))
        modelSettingsButton.imagePosition = .imageOnly

        for view in [icon, worldName, viewButton, openWorldButton, saveWorldButton, modelSettingsButton] {
            view.translatesAutoresizingMaskIntoConstraints = false
            surface.addSubview(view)
        }
        NSLayoutConstraint.activate([
            icon.leadingAnchor.constraint(equalTo: surface.leadingAnchor, constant: 11),
            icon.centerYAnchor.constraint(equalTo: surface.centerYAnchor),
            icon.widthAnchor.constraint(equalToConstant: 14),
            icon.heightAnchor.constraint(equalToConstant: 15),
            worldName.leadingAnchor.constraint(equalTo: icon.trailingAnchor, constant: 7),
            worldName.centerYAnchor.constraint(equalTo: surface.centerYAnchor),
            worldName.trailingAnchor.constraint(lessThanOrEqualTo: viewButton.leadingAnchor, constant: -8),
            viewButton.centerYAnchor.constraint(equalTo: surface.centerYAnchor),
            viewButton.trailingAnchor.constraint(equalTo: openWorldButton.leadingAnchor, constant: -4),
            viewButton.widthAnchor.constraint(equalToConstant: 107),
            openWorldButton.centerYAnchor.constraint(equalTo: surface.centerYAnchor),
            openWorldButton.trailingAnchor.constraint(equalTo: saveWorldButton.leadingAnchor, constant: -4),
            saveWorldButton.centerYAnchor.constraint(equalTo: surface.centerYAnchor),
            saveWorldButton.trailingAnchor.constraint(equalTo: modelSettingsButton.leadingAnchor, constant: -4),
            modelSettingsButton.trailingAnchor.constraint(equalTo: surface.trailingAnchor, constant: -8),
            modelSettingsButton.centerYAnchor.constraint(equalTo: surface.centerYAnchor),
            modelSettingsButton.widthAnchor.constraint(equalToConstant: 27)
        ])
    }

    private func updateViewButton(thirdPerson: Bool) {
        let title = thirdPerson ? "Third Person" : "First Person"
        guard viewButton.title != title else { return }
        let target = thirdPerson ? "First Person" : "Third Person"
        viewButton.title = title
        viewButton.setAccessibilityLabel("\(title). Switch to \(target)")
        viewButton.toolTip = "Switch to \(target) (V)"
    }

    @objc private func toggleView() {
        guard viewButton.isEnabled else { return }
        focusPlayer()
        viewToggleCount += 1
        do {
            try setMovementPaused(false)
        } catch {
            viewToggleCount -= 1
            showProblem("Could not switch the camera view.")
        }
    }

    private func configureLogPanel() {
        let surface = logSurface
        configureOverlay(logPanel, surface: surface, title: "Design Log", radius: 12)
        logPanel.hasShadow = false
        surface.layer?.backgroundColor = NSColor(srgbRed: 0.087, green: 0.099, blue: 0.085, alpha: 0.58).cgColor
        let title = NSTextField(labelWithString: "Design Log")
        title.font = .systemFont(ofSize: 12, weight: .semibold)
        title.textColor = NSColor.white.withAlphaComponent(0.95)
        logDot.wantsLayer = true
        logDot.layer?.cornerRadius = 3
        logChevron.image = NSImage(systemSymbolName: "chevron.up", accessibilityDescription: nil)
        logChevron.imageScaling = .scaleProportionallyDown
        logChevron.contentTintColor = NSColor.white.withAlphaComponent(0.90)
        logChevron.setAccessibilityElement(false)
        toggleLogButton.isBordered = false
        toggleLogButton.title = ""
        toggleLogButton.focusRingType = .none
        toggleLogButton.target = self
        toggleLogButton.action = #selector(toggleLog)
        toggleLogButton.setAccessibilityLabel("Hide Log")
        toggleLogButton.toolTip = "Hide Log"
        logView.isEditable = false
        logView.isSelectable = true
        logView.drawsBackground = false
        logView.isVerticallyResizable = true
        logView.isHorizontallyResizable = false
        logView.autoresizingMask = [.width]
        logView.textContainer?.widthTracksTextView = true
        logView.textContainer?.lineFragmentPadding = 0
        logView.textContainerInset = NSSize(width: 8, height: 6)
        logView.setAccessibilityLabel("Design progress and scene verification log")
        logScroll.documentView = logView
        logScroll.drawsBackground = false
        logScroll.hasVerticalScroller = true
        logScroll.autohidesScrollers = true
        logScroll.scrollerStyle = .overlay

        for view in [logDot, title, logChevron, logScroll, toggleLogButton] {
            view.translatesAutoresizingMaskIntoConstraints = false
            surface.addSubview(view)
        }
        let bottom = logScroll.bottomAnchor.constraint(equalTo: surface.bottomAnchor, constant: -9)
        logBodyConstraint = bottom
        NSLayoutConstraint.activate([
            logDot.leadingAnchor.constraint(equalTo: surface.leadingAnchor, constant: 13),
            logDot.centerYAnchor.constraint(equalTo: title.centerYAnchor),
            logDot.widthAnchor.constraint(equalToConstant: 6),
            logDot.heightAnchor.constraint(equalToConstant: 6),
            title.leadingAnchor.constraint(equalTo: logDot.trailingAnchor, constant: 7),
            title.centerYAnchor.constraint(equalTo: toggleLogButton.centerYAnchor),
            logChevron.trailingAnchor.constraint(equalTo: surface.trailingAnchor, constant: -13),
            logChevron.centerYAnchor.constraint(equalTo: title.centerYAnchor),
            logChevron.widthAnchor.constraint(equalToConstant: 11),
            logChevron.heightAnchor.constraint(equalToConstant: 11),
            toggleLogButton.topAnchor.constraint(equalTo: surface.topAnchor),
            toggleLogButton.leadingAnchor.constraint(equalTo: surface.leadingAnchor),
            toggleLogButton.trailingAnchor.constraint(equalTo: surface.trailingAnchor),
            toggleLogButton.heightAnchor.constraint(equalToConstant: OverlayLayout.logHeaderHeight),
            logScroll.leadingAnchor.constraint(equalTo: surface.leadingAnchor, constant: 9),
            logScroll.trailingAnchor.constraint(equalTo: surface.trailingAnchor, constant: -9),
            logScroll.topAnchor.constraint(equalTo: surface.topAnchor, constant: OverlayLayout.logHeaderHeight),
            bottom
        ])
    }

    @objc private func toggleLog() {
        guard toggleLogButton.isEnabled, logPanel.isVisible else { return }
        setLogExpanded(!logExpanded)
    }

    private func setLogExpanded(_ expanded: Bool) {
        logExpanded = expanded
        if let player { syncOverlays(in: gameCanvas(player.processIdentifier)) }
        if expanded { scrollLogToLatest() }
    }

    private func configureWorldButton(_ button: NSButton, title: String, label: String, symbol: String, action: Selector) {
        button.title = title
        button.setAccessibilityLabel(label)
        button.toolTip = label
        button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        button.imagePosition = .imageLeading
        button.bezelStyle = .rounded
        button.controlSize = .small
        button.target = self
        button.action = action
        button.isEnabled = false
    }

    // MARK: - Settings and world files

    private func loadModelSettings() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self, store = modelSettingsStore] in
            let result = Result { try store.load() }
            DispatchQueue.main.async {
                guard let self, !self.closing else { return }
                self.modelSettingsLoaded = true
                switch result {
                case .success(let configuration):
                    self.modelConfiguration = configuration
                case .failure(let error):
                    self.modelSettingsLoadError = error.localizedDescription
                }
                self.startInitialPlayerIfReady()
            }
        }
    }

    @objc private func openModelSettings() {
        guard modelSettingsButton.isEnabled, let player, let canvas = gameCanvas(player.processIdentifier) else { return }
        presentModelSettings(in: canvas)
    }

    private func presentModelSettings(in canvas: CanvasWindow, loadError: String? = nil) {
        guard modelSettingsView == nil, filePicker == nil, !closing, let surface = panel.contentView else { return }
        do {
            try setMovementPaused(true)
        } catch {
            showProblem("Could not pause navigation to open Model Settings.")
            return
        }

        let settings = ModelSettingsView(
            configuration: modelConfiguration, loadError: loadError, store: modelSettingsStore
        ) { [weak self] configuration in
            self?.closeModelSettings(configuration)
        }
        modelSettingsView = settings
        endPromptInput()
        surface.addSubview(settings, positioned: .above, relativeTo: promptBox)
        // Use the scene overlay as the single window for the settings and its surrounding controls.
        for (accessory, contents) in accessories {
            accessory.orderOut(nil)
            accessory.contentView = nil
            contents.autoresizingMask = []
            surface.addSubview(contents, positioned: .below, relativeTo: settings)
        }
        panel.hasShadow = false
        panel.setAccessibilityModal(true)
        updateWorldControls()
        syncOverlays(in: canvas)
        focusModelSettings()
        settings.beginEditing()
    }

    private func closeModelSettings(_ configuration: ModelConfiguration?) {
        guard !closing, let settings = modelSettingsView else { return }
        if let configuration { modelConfiguration = configuration }
        modelSettingsView = nil
        do {
            try setMovementPaused(updateState != .walking && updateState != .resuming)
        } catch {
            modelSettingsView = settings
            settings.showNavigationError()
            return
        }

        panel.makeFirstResponder(nil)
        panel.initialFirstResponder = nil
        panel.defaultButtonCell = nil
        panel.setAccessibilityModal(false)
        panel.hasShadow = true
        for (accessory, contents) in accessories {
            contents.removeFromSuperview()
            contents.isHidden = false
            accessory.contentView = contents
        }
        settings.clearKey()
        settings.removeFromSuperview()
        updateWorldControls()
        if let player { syncOverlays(in: gameCanvas(player.processIdentifier)) }
        focusPlayer()
    }

    private func focusModelSettings() {
        guard modelSettingsView != nil, panel.isVisible else { return }
        // The panel can receive keys while the player owns the menu bar.
        panel.makeKeyAndOrderFront(nil)
    }

    @objc private func openWorld() {
        guard openWorldButton.isEnabled, filePicker == nil else { return }
        let picker = NSOpenPanel()
        picker.title = "Open World"
        picker.prompt = "Open"
        picker.allowedContentTypes = [blendType]
        picker.allowsOtherFileTypes = false
        picker.canChooseDirectories = false
        picker.allowsMultipleSelection = false
        picker.directoryURL = worldFile?.deletingLastPathComponent()
        presentFilePicker(picker) { [weak self] answer in
            guard let self else { return }
            if answer == .OK, let url = picker.url {
                self.beginUpdate(.open(url))
            } else if self.updateState == .walking {
                self.focusPlayer()
            }
        }
    }

    @objc private func saveWorld() {
        guard saveWorldButton.isEnabled, filePicker == nil else { return }
        let picker = NSSavePanel()
        picker.title = "Save World"
        picker.prompt = "Save"
        picker.nameFieldLabel = "Save As:"
        picker.allowedContentTypes = [blendType]
        picker.allowsOtherFileTypes = false
        picker.canCreateDirectories = true
        picker.directoryURL = worldFile?.deletingLastPathComponent()
        picker.nameFieldStringValue = worldFile?.lastPathComponent ?? "Untitled World.blend"
        presentFilePicker(picker) { [weak self] answer in
            guard let self else { return }
            if answer == .OK, let url = picker.url { self.writeWorld(url) }
            self.focusPlayer()
        }
    }

    private func presentFilePicker(_ picker: NSSavePanel, completion: @escaping (NSApplication.ModalResponse) -> Void) {
        endPromptInput()
        filePicker = picker
        updateWorldControls()
        panel.level = .normal
        panel.makeKeyAndOrderFront(nil)
        picker.beginSheetModal(for: panel) { [weak self] answer in
            guard let self else { return }
            self.filePicker = nil
            if let player = self.player { self.syncOverlays(in: self.gameCanvas(player.processIdentifier)) }
            self.updateWorldControls()
            completion(answer)
        }
    }

    private func writeWorld(_ url: URL) {
        let id = UUID().uuidString
        do {
            try send(["command": "save_world", "path": url.path, "request_id": id])
            saveRequestID = id
            setStatus("Saving world…", tint: working)
        } catch {
            showProblem("Could not reach the background service. The world was not saved.")
        }
    }

    // MARK: - Backend and player

    private func send(_ request: [String: Any]) throws {
        guard service?.isRunning == true else { throw CocoaError(.fileNoSuchFile) }
        var payload = try JSONSerialization.data(withJSONObject: request)
        payload.append(10)
        try serviceInput.fileHandleForWriting.write(contentsOf: payload)
    }

    private func log(_ name: String) throws -> FileHandle {
        let url = state.appendingPathComponent(name)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        return handle
    }

    private func startService() throws {
        serviceLog = try log("service.log")
        let command = Process()
        command.executableURL = bundledPython ?? URL(fileURLWithPath: "/usr/bin/python3")
        var arguments = ["-I", "-B", resources.appendingPathComponent("backend/main.py").path, "--state", state.path]
        if bundledPython != nil {
            let editor = upbge.appendingPathComponent("Blender.app/Contents/MacOS/Blender")
            let player = upbge.appendingPathComponent("LightWorld.app/Contents/MacOS/Blenderplayer")
            arguments += ["--player", player.path]
            var environment = ProcessInfo.processInfo.environment
            environment["LIGHTWORLD_UPBGE"] = upbge.path
            environment["LIGHTWORLD_UPBGE_BLENDER"] = editor.path
            environment["SSL_CERT_FILE"] = resources.appendingPathComponent("blender-mcp/site-packages/certifi/cacert.pem").path
            command.environment = environment
        } else {
            arguments += ["--player-cache", playerCache.path]
        }
        command.arguments = arguments
        command.standardInput = serviceInput
        command.standardOutput = serviceOutput
        command.standardError = serviceLog
        command.terminationHandler = { [weak self] _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                guard let self, !self.closing else { return }
                self.showFatal("The background service stopped. See service.log for details.")
            }
        }
        try command.run()
        service = command
        serviceOutput.fileHandleForReading.readabilityHandler = { [weak self] source in
            let chunk = source.availableData
            if chunk.isEmpty {
                source.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async { self?.consume(chunk) }
        }
    }

    private func consume(_ chunk: Data) {
        stdoutBuffer.append(chunk)
        guard let newline = stdoutBuffer.lastIndex(of: 10) else { return }
        let packets = stdoutBuffer[..<newline].split(separator: 10)
        stdoutBuffer.removeSubrange(...newline)
        for packet in packets {
            guard !closing else { return }
            handleServiceEvent(packet)
        }
    }

    private func handleServiceEvent(_ packet: Data) {
        guard let event = (try? JSONSerialization.jsonObject(with: packet)) as? [String: Any],
              let type = event["event"] as? String else { return }
        switch type {
        case "boot":
            guard let path = event["blend_path"] as? String,
                  let executable = event["player_executable"] as? String else { return }
            initialPlayer = (path, URL(fileURLWithPath: executable))
            startInitialPlayerIfReady()
        case "stage":
            let message = event["message"] as? String ?? "Processing design…"
            let level = event["level"] as? String ?? "info"
            let tint = (level == "warning" || level == "error") ? failed : working
            if updateState == .designing, matchesRequest(event) {
                let phase = event["phase"] as? String
                if phase != "received" {
                    if let tag = logLabel(for: phase) { appendLog(message, tag: tag, tint: tint) }
                    setStatus(message, tint: tint)
                }
            } else if !hasScene {
                setStatus(message, tint: tint)
            }
        case "published":
            guard updateState == .designing, matchesRequest(event),
                  let path = event["blend_path"] as? String else { return }
            updateState = .loading
            pendingScenePath = path
            sceneDeadline = Date().addingTimeInterval(45)
            if let details = event["changes"] as? [String: Any] {
                logChanges(details)
            }
            setStatus("Loading scene…", tint: working)
        case "error":
            guard updateState == .designing, matchesRequest(event) else { return }
            let message = event["message"] as? String ?? "The operation did not complete."
            if let request = updateRequest, case .open = request.change, request.wasWalking {
                recoverWorldAfterError(message, tag: "Open Failed")
            } else if event["resume_world"] as? Bool == true {
                restorePrompt()
                recoverWorldAfterError(message, tag: logLabel(for: event["phase"] as? String) ?? "Error")
            } else {
                failUpdate(message)
            }
        case "saved":
            guard let id = event["request_id"] as? String, id == saveRequestID,
                  let path = event["path"] as? String else { return }
            saveRequestID = nil
            worldFile = URL(fileURLWithPath: path)
            worldName.stringValue = worldFile?.deletingPathExtension().lastPathComponent ?? "Saved World"
            let message = event["message"] as? String ?? "World saved."
            setStatus(message, tint: healthy)
            appendLog(message, tag: "Save World", tint: healthy)
        case "save_error":
            guard let id = event["request_id"] as? String, id == saveRequestID else { return }
            saveRequestID = nil
            let message = event["message"] as? String ?? "Could not save the world."
            setStatus(message, tint: failed)
            showLog(reset: false)
            appendLog(message, tag: "Save Failed", tint: failed)
        case "fatal":
            showFatal(event["message"] as? String ?? "LightWorld could not start.")
        default:
            break
        }
    }

    private func startInitialPlayerIfReady() {
        guard !closing, modelSettingsLoaded, player == nil, let initialPlayer else { return }
        self.initialPlayer = nil
        do {
            try startPlayer(initialPlayer.scene, executable: initialPlayer.executable)
            sceneDeadline = Date().addingTimeInterval(45)
            pendingScenePath = initialPlayer.scene
        } catch {
            showFatal("Could not start the LightWorld player. Error \((error as NSError).code).")
        }
    }

    private func startPlayer(_ path: String, executable: URL) throws {
        playerLog = try log("player.log")
        let command = Process()
        command.executableURL = executable
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let width = max(610, min(1280, Int(screen.width * 0.92)))
        let height = max(440, min(820, Int(screen.height * 0.89)))
        let primaryScreen = NSScreen.screens.first?.visibleFrame ?? screen
        let left = max(0, min(100, Int(primaryScreen.maxX) - width))
        let top = 100
        command.arguments = ["-w", String(width), String(height), String(left), String(top), path,
                             "-", "-ApplePersistenceIgnoreState", "YES"]
        var environment = ProcessInfo.processInfo.environment
        environment["LIGHTWORLD_STATE_DIR"] = state.path
        environment["LIGHTWORLD_AVATAR"] = resources.appendingPathComponent("runtime/avatar.blend").path
        let versions = (try? FileManager.default.contentsOfDirectory(at: blenderResources,
                                                                    includingPropertiesForKeys: nil)) ?? []
        let configs = versions.map { $0.appendingPathComponent("datafiles/colormanagement/config.ocio") }
        if let config = configs.first(where: { FileManager.default.isReadableFile(atPath: $0.path) }) {
            environment["BLENDER_OCIO"] = config.path
        }
        command.environment = environment
        command.standardInput = FileHandle.nullDevice
        command.standardOutput = playerLog
        command.standardError = playerLog
        command.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self, !self.closing else { return }
                if process.terminationReason == .exit && process.terminationStatus == 0 && self.hasScene {
                    NSApp.terminate(nil)
                } else {
                    self.showFatal("The UPBGE player stopped. See player.log for details.")
                }
            }
        }
        showLoadingPanel(size: CGSize(width: width, height: height), left: left, top: top, screen: primaryScreen)
        try command.run()
        player = command
        if modelConfiguration == nil { try setMovementPaused(true) }
        launchDeadline = Date().addingTimeInterval(35)
        setStatus("Starting navigation…", tint: working)
        canvasMouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { [weak self] _ in
            self?.canvasMouseDown()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.12, repeats: true) { [weak self] _ in
            self?.advance()
        }
    }

    private func advance() {
        guard !closing, let player else { return }
        let pid = player.processIdentifier
        let canvas = gameCanvas(pid)
        let path = state.appendingPathComponent("player.json")
        let snapshot = (try? Data(contentsOf: path)).flatMap {
            try? JSONDecoder().decode(PlayerSnapshot.self, from: $0)
        }.flatMap { $0.pid == pid ? $0 : nil }
        if let snapshot { updateViewButton(thirdPerson: snapshot.thirdPerson) }

        if !hasScene {
            alignLoadingPanel(with: canvas)
            guard let expected = pendingScenePath else { return }
            let needsModelSettings = modelConfiguration == nil
            if let canvas, snapshot?.status == "ready",
               snapshot?.blendPath == expected, snapshot?.paused == needsModelSettings {
                pendingScenePath = nil
                activeScenePath = expected
                setStatus("World ready", tint: healthy)
                syncOverlays(in: canvas)
                focusPlayer()
                if needsModelSettings {
                    presentModelSettings(in: canvas, loadError: modelSettingsLoadError)
                    modelSettingsLoadError = nil
                }
                dismissLoadingPanel()
            } else if canvas == nil, Date() > launchDeadline {
                showFatal("UPBGE could not show the navigation window. See player.log for details.")
            } else if Date() > sceneDeadline {
                showFatal("The initial scene could not load. See player.log for details.")
            }
            return
        }

        if let canvas, let displayedCanvas, canvas.windowFrame != displayedCanvas.windowFrame {
            startTrackingCanvasDrag()
        }
        syncOverlays(in: canvas)
        if panel.isKeyWindow && promptOwnsResponder {
            setPromptFocus(true)
        } else if typingSession != nil {
            endPromptInput()
        }
        if canvas != nil, filePicker == nil, let typingSession,
           snapshot?.status == "ready", snapshot?.blendPath == activeScenePath,
           snapshot?.canvasClickTypingSession == typingSession {
            focusPlayer()
        }

        switch updateState {
        case .pausing:
            if let request = updateRequest, let snapshot, snapshot.status == "ready", snapshot.paused == true,
               snapshot.blendPath == activeScenePath {
                updateRequest?.pose = snapshot
                submitChange(request, pid: pid)
            } else if Date() > sceneDeadline {
                failUpdate("UPBGE did not confirm the pause. The request was not sent.")
            }
        case .loading:
            guard let expected = pendingScenePath else { return }
            if snapshot?.status == "ready", snapshot?.blendPath == expected, snapshot?.paused == true {
                do {
                    try resumeMovement()
                    setStatus("Scene loaded.", tint: healthy)
                } catch {
                    failUpdate("The scene loaded. Navigation is still locked.", canRetry: false)
                }
            } else if snapshot?.status == "load_error", snapshot?.targetBlendPath == expected {
                failUpdate(snapshot?.message ?? "UPBGE could not load the new scene.", canRetry: false)
            } else if Date() > sceneDeadline {
                failUpdate("The scene was saved. UPBGE did not confirm it loaded in time.", canRetry: false)
            }
        case .resuming:
            if let expected = pendingScenePath, snapshot?.status == "ready",
               snapshot?.blendPath == expected, snapshot?.paused == (modelSettingsView != nil) {
                finishUpdate(scene: expected, pid: pid)
            } else if Date() > sceneDeadline {
                failUpdate("The scene loaded. UPBGE did not confirm that navigation resumed.", canRetry: false)
            }
        case .blocked, .designing, .walking:
            break
        }
    }

    private func matchesRequest(_ event: [String: Any]) -> Bool {
        guard let id = event["request_id"] as? String else { return true }
        return id == updateRequest?.id
    }

    private func setMovementPaused(_ paused: Bool, typingSession: String? = nil) throws {
        guard let player, player.isRunning else { throw CocoaError(.fileNoSuchFile) }
        var movement: [String: Any] = [
            "pid": player.processIdentifier, "paused": paused || modelSettingsView != nil,
            "view_toggle_count": viewToggleCount
        ]
        if modelSettingsView == nil { movement["typing_session"] = typingSession }
        let data = try JSONSerialization.data(withJSONObject: movement)
        try data.write(to: state.appendingPathComponent("movement.json"), options: .atomic)
    }

    private func submitChange(_ request: UpdateRequest, pid: Int32) {
        var message: [String: Any] = ["request_id": request.id, "player_pid": pid]
        let status: String
        switch request.change {
        case .open(let url):
            message["command"] = "open_world"
            message["path"] = url.path
            status = "Opening world. Navigation is paused."
        case .design(let text, let configuration):
            message["command"] = "submit"
            message["prompt"] = text
            message["model_settings"] = configuration.requestFields
            status = "Processing design. Navigation is paused."
        }
        do {
            try send(message)
            updateState = .designing
            setStatus(status, tint: working)
        } catch {
            failUpdate("Could not reach the background service.")
        }
    }

    private func resumeMovement() throws {
        try setMovementPaused(false)
        updateState = .resuming
        sceneDeadline = Date().addingTimeInterval(6)
    }

    private func finishUpdate(scene: String, pid: Int32) {
        let change = updateRequest?.change
        activeScenePath = scene
        pendingScenePath = nil
        updateRequest = nil
        updateState = .walking
        let navigation = modelSettingsView == nil ? " Navigation resumed." : ""

        if let error = pendingRecoveryError {
            pendingRecoveryError = nil
            setStatus(error, tint: failed)
        } else if case .open(let source)? = change {
            worldFile = source
            worldName.stringValue = source.deletingPathExtension().lastPathComponent
            setPromptText("")
            let message = "World opened.\(navigation)"
            appendLog(message, tag: "Open World", tint: healthy)
            setStatus(message, tint: healthy)
        } else {
            if worldFile == nil { worldName.stringValue = "Untitled World" }
            let message = "Design applied.\(navigation)"
            appendLog(message, tag: "Result", tint: healthy)
            setStatus(message, tint: healthy)
            setLogExpanded(false)
        }
        try? send(["command": "scene_loaded", "player_pid": pid])
        focusPlayer()
    }

    private func recoverWorldAfterError(_ message: String, tag: String) {
        appendLog(message, tag: tag, tint: failed)
        do {
            try resumeMovement()
            pendingRecoveryError = message
            pendingScenePath = activeScenePath
            setStatus("Returning to the current world…", tint: working)
        } catch {
            failUpdate("The operation did not complete and navigation is still locked.", canRetry: false)
        }
    }

    private func restorePrompt() {
        if prompt.string.isEmpty, case .design(let text, _)? = updateRequest?.change {
            setPromptText(text)
        }
    }

    private func failUpdate(_ message: String, canRetry: Bool = true) {
        let movementPaused = updateRequest?.pose != nil
        if canRetry { restorePrompt() }
        updateState = .blocked(canRetry: canRetry)
        updateRequest = nil
        pendingScenePath = nil
        pendingRecoveryError = nil
        appendLog(message, tag: "Error", tint: failed)
        let recovery: String
        if !canRetry {
            recovery = "Could not confirm the scene status. Quit and reopen LightWorld."
        } else if movementPaused {
            recovery = "Navigation is still paused. Edit the prompt and send it again."
        } else {
            recovery = "Edit the prompt and send it again."
        }
        appendLog(recovery, tag: "LightWorld", tint: failed)
        setStatus(recovery, tint: failed)
    }

    private func logLabel(for phase: String?) -> String? {
        switch phase {
        case "viewer": return "Camera"
        case "render_before": return "Render"
        case "model", "visual_plan", "audit", "visual_audit": return "API"
        case "export": return "Blender"
        case "audit_retry", "visual_retry", "review_retry": return "Retry"
        default: return nil
        }
    }

    private func appendLog(_ message: String, tag: String, tint: NSColor) {
        guard let storage = logView.textStorage else { return }
        let follow = storage.length == 0 || logView.bounds.maxY <= logScroll.documentVisibleRect.maxY + 20
        let prefix = "\(clock.string(from: Date()))  \(tag)  "
        let tagColor = tint.blended(withFraction: 0.36, of: .white) ?? tint
        storage.beginEditing()
        storage.append(NSAttributedString(string: prefix, attributes: [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .semibold), .foregroundColor: tagColor
        ]))
        storage.append(NSAttributedString(string: message + "\n", attributes: [
            .font: NSFont.systemFont(ofSize: 12), .foregroundColor: NSColor.white
        ]))
        storage.endEditing()
        if follow && !logScroll.isHidden { scrollLogToLatest() }
    }

    private func scrollLogToLatest() {
        logView.scrollRangeToVisible(NSRange(location: logView.textStorage?.length ?? 0, length: 0))
    }

    private func showLog(reset: Bool) {
        if reset { logView.string = "" }
        setLogExpanded(true)
    }

    private func logChanges(_ changes: [String: Any]) {
        for (key, label) in [("added", "Added"), ("updated", "Updated"), ("removed", "Removed")] {
            let items = changes[key] as? [[String: Any]] ?? []
            for item in items {
                guard let identifier = item["id"] as? String else { continue }
                var line = identifier
                if let position = item["position"] as? [Double], position.count == 3 {
                    let x = position[0], y = position[1], z = position[2]
                    line += String(format: " · X %.1f m / Y %.1f m / Z %.1f m", x, y, z)
                    if let direction = relativeLocation(x: x, y: y) {
                        line += " · " + direction
                    }
                }
                appendLog(line, tag: label, tint: healthy)
            }
        }
        let settings = changes["settings"] as? [[String: Any]] ?? []
        for item in settings {
            guard let label = item["label"] as? String else { continue }
            appendLog(label, tag: "Updated", tint: healthy)
        }
    }

    private func relativeLocation(x: Double, y: Double) -> String? {
        guard let pose = updateRequest?.pose, let point = pose.position, point.count >= 2, let yaw = pose.yaw else {
            return nil
        }
        let dx = x - point[0], dy = y - point[1]
        let distance = hypot(dx, dy)
        if distance < 0.75 { return "Near your position" }
        let forward = -dx * sin(yaw) + dy * cos(yaw)
        let right = dx * cos(yaw) + dy * sin(yaw)
        let directions = ["Ahead", "Ahead right", "Right", "Behind right", "Behind", "Behind left", "Left", "Ahead left"]
        let sector = (Int((atan2(right, forward) / (.pi / 4)).rounded()) + 8) % 8
        return directions[sector] + String(format: " %.1f m", distance)
    }

    private func gameCanvas(_ pid: pid_t) -> CanvasWindow? {
        guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                       kCGNullWindowID) as? [[String: Any]] else { return nil }
        let normalWindows = windows.filter { $0[kCGWindowLayer as String] as? Int == 0 }
        for (index, window) in normalWindows.enumerated() where window[kCGWindowOwnerPID as String] as? Int32 == pid {
            guard let number = window[kCGWindowNumber as String] as? Int,
                  let frame = canvasWindowFrame(window) else { continue }
            let visibleInFront = normalWindows.prefix(index).compactMap { $0[kCGWindowNumber as String] as? Int }
            return CanvasWindow(frame: canvasContentFrame(frame), windowFrame: frame, number: number,
                                windowsInFront: visibleInFront)
        }
        return nil
    }

    private func canvasWindowFrame(_ description: [String: Any]) -> CGRect? {
        guard let screenTop = NSScreen.screens.first?.frame.maxY,
              let data = description[kCGWindowBounds as String] as? NSDictionary,
              let bounds = CGRect(dictionaryRepresentation: data as CFDictionary),
              bounds.width > 220, bounds.height > 175 else { return nil }
        return CGRect(x: bounds.minX, y: screenTop - bounds.maxY, width: bounds.width, height: bounds.height)
    }

    private func canvasContentFrame(_ frame: CGRect) -> CGRect {
        let fillsScreen = NSScreen.screens.contains { $0.frame.equalTo(frame) }
        let titleBarHeight: CGFloat = fillsScreen ? 0 : 28
        return CGRect(x: frame.minX, y: frame.minY, width: frame.width, height: frame.height - titleBarHeight)
    }

    private func canvasMouseDown() {
        guard !closing, let canvas = displayedCanvas else { return }
        let point = NSEvent.mouseLocation
        guard canvas.windowFrame.contains(point) else { return }
        if point.y >= canvas.frame.maxY || !canvas.windowFrame.insetBy(dx: 6, dy: 6).contains(point) {
            startTrackingCanvasDrag()
        }
    }

    private func startTrackingCanvasDrag() {
        guard canvasDragTimer == nil else { return }
        canvasLastMove = ProcessInfo.processInfo.systemUptime
        let timer = Timer(timeInterval: 1.0 / 120, repeats: true) { [weak self] _ in
            self?.followCanvasDrag()
        }
        canvasDragTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func stopTrackingCanvasDrag() {
        canvasDragTimer?.invalidate()
        canvasDragTimer = nil
    }

    private func followCanvasDrag() {
        guard !closing, let canvas = displayedCanvas else {
            stopTrackingCanvasDrag()
            return
        }
        let now = ProcessInfo.processInfo.systemUptime
        // During a drag, ask the window server for the player window alone.
        var windowID = UnsafeRawPointer(bitPattern: canvas.number)
        if let ids = CFArrayCreate(nil, &windowID, 1, nil),
           let windows = CGWindowListCreateDescriptionFromArray(ids) as? [[String: Any]],
           let window = windows.first, let frame = canvasWindowFrame(window), frame != canvas.windowFrame {
            canvasLastMove = now
            let content = canvasContentFrame(frame)
            displayedCanvas = CanvasWindow(frame: content, windowFrame: frame, number: canvas.number,
                                           windowsInFront: canvas.windowsInFront)
            positionOverlays(in: content)
        }
        if NSEvent.pressedMouseButtons & 1 == 0, now - canvasLastMove >= 0.08 {
            stopTrackingCanvasDrag()
            restoreSettingsFocus()
        }
    }

    private func restoreSettingsFocus() {
        guard let player, NSEvent.pressedMouseButtons & 1 == 0,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == player.processIdentifier else { return }
        focusModelSettings()
    }

    @objc private func workspaceDidActivateApplication(_ notification: Notification) {
        DispatchQueue.main.async { [weak self] in
            guard let self, !self.closing, let player = self.player else { return }
            let canvas = self.gameCanvas(player.processIdentifier)
            guard self.hasScene else {
                self.alignLoadingPanel(with: canvas)
                return
            }
            self.syncOverlays(in: canvas)
            self.restoreSettingsFocus()
        }
    }

    private func showLoadingPanel(size: CGSize, left: Int, top: Int, screen: CGRect) {
        let loading = AccessoryPanel(contentRect: CGRect(origin: .zero, size: size),
                                     styleMask: [.titled, .nonactivatingPanel], backing: .buffered, defer: false)
        loading.title = "LightWorld"
        loading.appearance = NSAppearance(named: .darkAqua)
        loading.backgroundColor = NSColor(srgbRed: 0.28, green: 0.35, blue: 0.43, alpha: 1)
        loading.hidesOnDeactivate = false
        loading.collectionBehavior = [.transient, .ignoresCycle]
        loading.animationBehavior = .none
        loading.level = .floating
        // Position the panel where UPBGE will open its window on the primary display.
        let bottom = max(screen.minY, screen.height - loading.frame.height - CGFloat(top))
        loading.setFrameOrigin(NSPoint(x: CGFloat(left), y: bottom))
        if let content = loading.contentView {
            let label = NSTextField(labelWithString: "Opening world…")
            label.font = .systemFont(ofSize: 15, weight: .medium)
            label.textColor = .white
            label.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(label)
            NSLayoutConstraint.activate([
                label.centerXAnchor.constraint(equalTo: content.centerXAnchor),
                label.centerYAnchor.constraint(equalTo: content.centerYAnchor),
            ])
        }
        loadingPanel = loading
        loading.orderFrontRegardless()
    }

    private func alignLoadingPanel(with canvas: CanvasWindow?) {
        guard let loading = loadingPanel else { return }
        let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier
        let isForeground = foreground == getpid() || foreground == player?.processIdentifier
        let level: NSWindow.Level = isForeground ? .floating : .normal
        if loading.level != level { loading.level = level }
        guard let canvas else { return }
        if loading.frame != canvas.windowFrame {
            loading.setFrame(canvas.windowFrame, display: true)
        }
        if !isForeground, !canvas.windowsInFront.contains(loading.windowNumber) {
            loading.order(.above, relativeTo: canvas.number)
        }
    }

    private func dismissLoadingPanel() {
        loadingPanel?.orderOut(nil)
        loadingPanel = nil
    }

    private func syncOverlays(in canvas: CanvasWindow?) {
        guard let player, let canvas, !NSRunningApplication.current.isHidden else {
            displayedCanvas = nil
            place(toolbarPanel, at: nil)
            place(logPanel, at: nil)
            if filePicker == nil && panel.attachedSheet == nil { place(panel, at: nil) }
            return
        }
        displayedCanvas = canvas
        let overlays = positionOverlays(in: canvas.frame)
        let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier
        let isForeground = foreground == player.processIdentifier || foreground == getpid()
        let level: NSWindow.Level = isForeground ? .floating : .normal
        let desiredOrder = overlays.map(\.windowNumber)
        let overlayNumbers = Set(desiredOrder)
        let nextToCanvas = Array(canvas.windowsInFront.reversed().prefix { overlayNumbers.contains($0) })
        var previousWindow = canvas.number
        for overlay in overlays {
            let levelChanged = overlay.level != level
            if levelChanged { overlay.level = level }
            if isForeground {
                if !overlay.isVisible { overlay.orderFrontRegardless() }
            } else if levelChanged || nextToCanvas != desiredOrder {
                overlay.order(.above, relativeTo: previousWindow)
            }
            previousWindow = overlay.windowNumber
        }
    }

    @discardableResult
    private func positionOverlays(in frame: CGRect) -> [NSPanel] {
        let settingsOpen = modelSettingsView != nil
        let sheetOpen = filePicker != nil || panel.attachedSheet != nil
        let layout = OverlayLayout(canvas: frame, promptTextHeight: promptScroll.textHeight,
                                   logExpanded: logExpanded, settingsOpen: settingsOpen)
        let hostFrame = settingsOpen ? frame : layout.prompt
        let bodyChanged = logScroll.isHidden == layout.showsLogBody
        if bodyChanged && !layout.showsLogBody {
            logBodyConstraint?.isActive = false
        }
        if !sheetOpen {
            place(promptBox, at: layout.prompt, in: hostFrame)
            promptScroll.updateScrolling(viewportHeight: layout.prompt.height - 2 * OverlayLayout.promptVerticalPadding)
        }
        if let settings = modelSettingsView {
            place(settings, at: hostFrame, in: hostFrame)
            place(toolbarSurface, at: layout.toolbar, in: hostFrame)
            place(logSurface, at: layout.log, in: hostFrame)
            settings.placeCard(at: layout.settingsFrame(in: frame)
                .offsetBy(dx: -hostFrame.minX, dy: -hostFrame.minY))
        }

        var overlays: [(NSPanel, CGRect?)] = settingsOpen ? [] : [(toolbarPanel, layout.toolbar), (logPanel, layout.log)]
        if !sheetOpen { overlays.append((panel, hostFrame)) }
        for (overlay, frame) in overlays { place(overlay, at: frame) }
        if bodyChanged {
            logScroll.isHidden = !layout.showsLogBody
            logBodyConstraint?.isActive = layout.showsLogBody
            let label = layout.showsLogBody ? "Hide Log" : "Show Log"
            logChevron.image = NSImage(systemSymbolName: layout.showsLogBody ? "chevron.up" : "chevron.down",
                                       accessibilityDescription: nil)
            toggleLogButton.setAccessibilityLabel(label)
            toggleLogButton.toolTip = label
        }
        return overlays.compactMap { overlay, frame in frame == nil ? nil : overlay }
    }

    private func place(_ overlay: NSPanel, at frame: CGRect?) {
        guard let frame else {
            if overlay.isVisible { overlay.orderOut(nil) }
            return
        }
        let aligned = CGRect(x: frame.minX.rounded(), y: frame.minY.rounded(),
                             width: frame.width.rounded(), height: frame.height.rounded())
        let current = overlay.frame
        guard current != aligned else { return }
        if current.size == aligned.size {
            overlay.setFrameOrigin(aligned.origin)
        } else {
            overlay.setFrame(aligned, display: overlay.isVisible)
        }
    }

    private func place(_ view: NSView, at frame: CGRect?, in hostFrame: CGRect) {
        let hidden = frame == nil
        if view.isHidden != hidden { view.isHidden = hidden }
        guard let frame else { return }
        let localFrame = frame.offsetBy(dx: -hostFrame.minX, dy: -hostFrame.minY)
        if view.frame != localFrame { view.frame = localFrame }
    }

    private func focusPlayer() {
        guard modelSettingsView == nil else { return }
        let panelHadKey = panel.isKeyWindow
        endPromptInput()
        guard let player, let application = NSRunningApplication(processIdentifier: player.processIdentifier),
              panelHadKey || !application.isActive else { return }
        if #available(macOS 14.0, *) {
            NSApp.yieldActivation(to: application)
            application.activate()
        } else {
            application.activate(options: [.activateIgnoringOtherApps])
        }
    }

    private var promptOwnsResponder: Bool {
        panel.firstResponder === prompt
    }

    private func endPromptInput() {
        setPromptFocus(false)
        if promptOwnsResponder { panel.makeFirstResponder(nil) }
    }

    private func setPromptFocus(_ focused: Bool) {
        if focused && (!prompt.isEditable || !panel.isKeyWindow || closing) { return }
        guard focused != (typingSession != nil) else { return }
        typingSession = focused ? UUID().uuidString : nil
        if !closing && (updateState == .walking || updateState.isBlocked) {
            do {
                try setMovementPaused(focused || updateState.isBlocked, typingSession: typingSession)
            } catch {
                showProblem(focused ? "Could not pause navigation." : "Could not resume navigation. Reopen LightWorld.")
            }
        }
        updateInputAppearance()
    }

    private func updateInputAppearance() {
        let mode: InputMode
        if modelSettingsLoaded && modelConfiguration == nil {
            mode = .configurationRequired
        } else if typingSession != nil && prompt.isEditable {
            mode = .typing
        } else if hasScene && updateState == .walking {
            mode = .roaming
        } else {
            mode = .paused
        }
        let tint: NSColor
        let symbol: String
        let help: String
        switch mode {
        case .roaming:
            tint = NSColor(srgbRed: 0.67, green: 0.72, blue: 0.62, alpha: 1)
            symbol = "pencil"
            help = "Click to edit the design prompt"
        case .typing:
            tint = editingTint
            symbol = "pencil"
            help = "Click Send or press Return to submit. Press Shift+Return to add a line."
        case .paused:
            tint = working
            symbol = "hourglass"
            help = prompt.isEditable ? "Click to revise the design prompt" : "See the Design Log for progress"
        case .configurationRequired:
            tint = working
            symbol = "gearshape"
            help = "Open Model Settings in the toolbar"
        }
        let placeholder = mode == .configurationRequired
            ? "Open Model Settings to enable prompts"
            : "Describe what you want to create or change…"
        if prompt.placeholder?.string != placeholder {
            prompt.placeholder = NSAttributedString(
                string: placeholder,
                attributes: [.foregroundColor: editingTint.withAlphaComponent(0.63),
                             .font: NSFont.systemFont(ofSize: 16)]
            )
            prompt.setAccessibilityPlaceholderValue(placeholder)
        }
        let isTyping = mode == .typing
        modeIcon.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 13, weight: .semibold))
        modeIcon.contentTintColor = tint
        promptBox.layer?.backgroundColor = isTyping
            ? NSColor(srgbRed: 0.19, green: 0.205, blue: 0.163, alpha: 1).cgColor
            : NSColor(srgbRed: 0.128, green: 0.143, blue: 0.117, alpha: 1).cgColor
        promptBox.layer?.borderWidth = isTyping ? 1.5 : 1
        promptBox.layer?.borderColor = tint.withAlphaComponent(isTyping ? 0.73 : 0.14).cgColor
        prompt.setAccessibilityHelp(help)
        updateSendButton()
    }

    private var promptText: String {
        prompt.string.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func setPromptText(_ text: String) {
        prompt.string = text
        promptDidChange()
    }

    private func promptDidChange() {
        prompt.needsDisplay = true
        promptScroll.measureText()
        updateSendButton()
    }

    private var canSubmitPrompt: Bool {
        !closing && prompt.isEditable && saveRequestID == nil && service?.isRunning == true
    }

    private func updateSendButton() {
        sendButton.isEnabled = canSubmitPrompt && !promptText.isEmpty
    }

    private func setStatus(_ message: String, tint: NSColor) {
        logDot.layer?.backgroundColor = tint.cgColor
        logPanel.setAccessibilityHelp(message)
        worldName.toolTip = worldName.stringValue
        updateWorldControls()
    }

    private func showProblem(_ message: String) {
        showLog(reset: false)
        appendLog(message, tag: "Notice", tint: failed)
        setStatus(message, tint: failed)
    }

    private func updateWorldControls() {
        let isWalking = updateState == .walking
        let settingsOpen = modelSettingsView != nil
        let dialogClosed = filePicker == nil && !settingsOpen
        let worldAvailable = hasScene && saveRequestID == nil && dialogClosed
        let canEditPrompt = hasScene && dialogClosed && updateState.allowsUpdate && modelConfiguration != nil
        prompt.isEditable = canEditPrompt
        prompt.isSelectable = canEditPrompt
        panel.allowsKey = canEditPrompt || filePicker != nil || settingsOpen
        viewButton.isEnabled = worldAvailable && isWalking
        openWorldButton.isEnabled = worldAvailable && updateState.allowsUpdate
        saveWorldButton.isEnabled = worldAvailable && isWalking
        modelSettingsButton.isEnabled = hasScene && modelSettingsLoaded && dialogClosed
        toggleLogButton.isEnabled = !settingsOpen
        updateInputAppearance()
    }

    private func showFatal(_ message: String) {
        guard !closing else { return }
        closing = true
        timer?.invalidate()
        stopTrackingCanvasDrag()
        dismissLoadingPanel()
        panel.orderOut(nil)
        toolbarPanel.orderOut(nil)
        logPanel.orderOut(nil)
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "LightWorld"
        alert.informativeText = message
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    func textView(_ textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        guard textView === prompt, !textView.hasMarkedText() else { return false }
        if selector == #selector(NSResponder.cancelOperation(_:)) {
            focusPlayer()
            return true
        }
        let shift = NSApp.currentEvent?.modifierFlags.contains(.shift) == true
        let newline = selector == #selector(NSResponder.insertNewline(_:))
        let lineBreak = selector == #selector(NSResponder.insertLineBreak(_:))
            || selector == #selector(NSResponder.insertNewlineIgnoringFieldEditor(_:))
        guard newline || (shift && lineBreak) else { return false }
        if shift {
            textView.insertNewline(nil)
        } else {
            submitPrompt()
        }
        return true
    }

    @objc private func submitPrompt() {
        guard canSubmitPrompt, let modelConfiguration else { return }
        prompt.unmarkText()
        let message = promptText
        guard !message.isEmpty else { return }
        guard message.unicodeScalars.count <= 2000 else {
            showProblem("Design prompts can contain up to 2,000 characters.")
            return
        }
        beginUpdate(.design(prompt: message, configuration: modelConfiguration))
    }

    private func beginUpdate(_ change: WorldChange) {
        guard hasScene, saveRequestID == nil, filePicker == nil, modelSettingsView == nil,
              updateState.allowsUpdate else { return }
        do {
            try setMovementPaused(true)
            updateRequest = UpdateRequest(change: change, wasWalking: updateState == .walking)
            updateState = .pausing
            sceneDeadline = Date().addingTimeInterval(6)
            showLog(reset: true)
            switch change {
            case .design(let text, _):
                appendLog(text, tag: "Prompt", tint: healthy)
            case .open:
                appendLog("Opening world…", tag: "Open World", tint: working)
            }
            endPromptInput()
            if case .design = change { setPromptText("") }
            setStatus("Pausing navigation…", tint: working)
        } catch {
            showProblem("Could not pause navigation. The request was not sent.")
        }
    }
}

@main
enum LightWorldApplication {
    @MainActor
    static func main() {
        signal(SIGPIPE, SIG_IGN)
        let app = NSApplication.shared
        let delegate = LightWorld()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
