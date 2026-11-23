import AppKit

guard CommandLine.arguments.count == 3,
      let preview = NSImage(contentsOfFile: CommandLine.arguments[1]) else {
    FileHandle.standardError.write(Data("Usage: Icon.swift <preview.png> <output.iconset>\n".utf8))
    exit(2)
}
let output = URL(fileURLWithPath: CommandLine.arguments[2])
let base = NSSize(width: 1024, height: 1024)
// Draw the approved artwork inside the icon tile, without the preview surround.
let artwork = NSRect(x: 111, y: 110, width: 1030, height: 1030)
let tile = NSRect(x: 77, y: 77, width: 870, height: 870)
let art = NSImage(size: base, flipped: false) { _ in
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current?.imageInterpolation = .high
    NSBezierPath(roundedRect: tile, xRadius: 213, yRadius: 213).addClip()
    preview.draw(in: tile, from: artwork, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    return true
}
for pointSize in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = pointSize * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                   bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                   colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        let context = NSGraphicsContext(bitmapImageRep: rep)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        context.imageInterpolation = .high
        art.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 1 ? "" : "@2x"
        let name = "icon_\(pointSize)x\(pointSize)\(suffix).png"
        try rep.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent(name))
    }
}
