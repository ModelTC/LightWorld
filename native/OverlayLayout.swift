import CoreGraphics

struct OverlayLayout {
    static let promptEditorMinimumHeight: CGFloat = 28
    static let promptEditorMaximumHeight: CGFloat = 144
    static let promptVerticalPadding: CGFloat = 17
    static let promptHeight = promptEditorMinimumHeight + 2 * promptVerticalPadding
    static let toolbarWidth: CGFloat = 413
    static let toolbarHeight: CGFloat = 42
    static let logWidth: CGFloat = 302
    static let logHeaderHeight: CGFloat = 40
    private static let margin: CGFloat = 14
    private static let gap: CGFloat = 10
    private static let settingsMinimumWidth: CGFloat = 236
    private static let settingsMinimumHeight: CGFloat = 150

    let prompt: CGRect
    let toolbar: CGRect?
    let log: CGRect?
    let showsLogBody: Bool

    init(canvas: CGRect, promptTextHeight: CGFloat, logExpanded: Bool, settingsOpen: Bool) {
        let margin = Self.margin
        let gap = Self.gap
        let usableWidth = max(1, canvas.width - 2 * margin)
        let promptWidth = min(620, usableWidth)
        let panelPadding = 2 * Self.promptVerticalPadding
        let topOverlayHeight = settingsOpen ? Self.settingsMinimumHeight : Self.logHeaderHeight
        let availableHeight = canvas.height - 2 * margin - Self.toolbarHeight - topOverlayHeight - 2 * gap
        let heightLimit = max(Self.promptHeight, min(availableHeight, Self.promptEditorMaximumHeight + panelPadding))
        let promptHeight = min(heightLimit, max(Self.promptHeight, promptTextHeight + panelPadding))
        let prompt = CGRect(x: canvas.midX - promptWidth / 2, y: canvas.minY + margin,
                            width: promptWidth, height: promptHeight)
        self.prompt = prompt

        let top = canvas.maxY - margin
        let toolbar = CGRect(x: canvas.minX + margin, y: top - Self.toolbarHeight,
                             width: min(usableWidth, Self.toolbarWidth), height: Self.toolbarHeight)
        self.toolbar = toolbar.minY >= prompt.maxY + gap ? toolbar : nil

        var logFrame: CGRect?
        var bodyVisible = false
        if let toolbar = self.toolbar {
            let logTop = toolbar.minY - gap
            let availableHeight = logTop - prompt.maxY - gap
            let expandedHeight = min(237, availableHeight)
            if logExpanded && expandedHeight >= 90 {
                let availableWidth = settingsOpen ? max(132, usableWidth - Self.settingsMinimumWidth - gap) : usableWidth
                logFrame = CGRect(x: toolbar.minX, y: logTop - expandedHeight,
                                  width: min(Self.logWidth, toolbar.width, availableWidth), height: expandedHeight)
                bodyVisible = true
            } else if availableHeight >= Self.logHeaderHeight {
                logFrame = CGRect(x: toolbar.minX, y: logTop - Self.logHeaderHeight,
                                  width: min(132, toolbar.width), height: Self.logHeaderHeight)
            }
        }
        self.log = logFrame
        showsLogBody = bodyVisible
    }

    func settingsFrame(in canvas: CGRect) -> CGRect {
        let inset = canvas.insetBy(dx: Self.margin, dy: Self.margin)
        let logEdge = log.map { $0.maxX + Self.gap } ?? inset.minX
        let left = min(logEdge, inset.maxX - min(Self.settingsMinimumWidth, inset.width))
        let bottom = prompt.maxY + Self.gap
        let top = (toolbar?.minY ?? inset.maxY) - Self.gap
        let width = min(500, inset.maxX - left)
        let height = min(212, inset.height, max(Self.settingsMinimumHeight, top - bottom))
        let x = max(left, min(inset.maxX - width, canvas.midX - width / 2))
        let y = max(inset.minY, min(inset.maxY - height, (top + bottom - height) / 2))
        return CGRect(x: x, y: y, width: width, height: height)
    }
}
