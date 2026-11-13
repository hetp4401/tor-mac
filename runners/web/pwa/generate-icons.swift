// Export the code-native Frame mark (icon.svg) using macOS vector drawing.
// Run from the repository root: swift runners/web/pwa/generate-icons.swift
import AppKit
import Foundation
let directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
func color(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> NSColor { NSColor(srgbRed:r/255, green:g/255, blue:b/255, alpha:1) }
for (name, size) in [("apple-touch-icon.png",180),("icon-192.png",192),("icon-512.png",512),("icon-512-maskable.png",512)] {
    let image = NSBitmapImageRep(bitmapDataPlanes:nil, pixelsWide:size, pixelsHigh:size, bitsPerSample:8, samplesPerPixel:4, hasAlpha:true, isPlanar:false, colorSpaceName:.deviceRGB, bytesPerRow:0, bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState()
    let context = NSGraphicsContext(bitmapImageRep:image)!
    NSGraphicsContext.current = context
    context.shouldAntialias = true
    let scale = CGFloat(size)/512
    let transform = AffineTransform(scale:scale)
    let background = NSBezierPath(rect:NSRect(x:0,y:0,width:512,height:512))
    background.transform(using:transform)
    color(11,13,16).setFill(); background.fill()
    let tile = NSBezierPath(roundedRect:NSRect(x:64,y:64,width:384,height:384), xRadius:104,yRadius:104)
    tile.transform(using:transform)
    color(128,84,245).setFill(); tile.fill()
    let glyph = NSBezierPath()
    glyph.move(to:NSPoint(x:205,y:349))
    glyph.curve(to:NSPoint(x:195,y:342), controlPoint1:NSPoint(x:198,y:354), controlPoint2:NSPoint(x:195,y:352))
    glyph.line(to:NSPoint(x:195,y:170))
    glyph.curve(to:NSPoint(x:206,y:164), controlPoint1:NSPoint(x:195,y:157), controlPoint2:NSPoint(x:195,y:157))
    glyph.line(to:NSPoint(x:350,y:248))
    glyph.curve(to:NSPoint(x:350,y:264), controlPoint1:NSPoint(x:363,y:256), controlPoint2:NSPoint(x:363,y:256))
    glyph.close()
    glyph.transform(using:transform)
    color(255,255,255).setFill(); glyph.fill()
    NSGraphicsContext.restoreGraphicsState()
    try image.representation(using:.png,properties:[:])!.write(to:directory.appendingPathComponent(name))
    print("\(name): \(size)×\(size)")
}
