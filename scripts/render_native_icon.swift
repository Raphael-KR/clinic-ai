import AppKit
let output = CommandLine.arguments[1]
let sizes = [16,32,64,128,256,512,1024]
try FileManager.default.createDirectory(atPath:output,withIntermediateDirectories:true)
for size in sizes {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:size,pixelsHigh:size,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep:bitmap)
    let scale = CGFloat(size)/1024
    let transform = AffineTransform(scale:scale); (transform as NSAffineTransform).concat()
    let background = NSBezierPath(roundedRect:NSRect(x:60,y:60,width:904,height:904),xRadius:204,yRadius:204)
    NSGradient(starting:NSColor(srgbRed:0.03,green:0.35,blue:0.39,alpha:1),ending:NSColor(srgbRed:0.16,green:0.78,blue:0.70,alpha:1))!.draw(in:background,angle:60)
    NSColor.white.withAlphaComponent(0.22).setFill()
    NSBezierPath(roundedRect:NSRect(x:196,y:204,width:632,height:632),xRadius:150,yRadius:150).fill()
    NSColor.white.withAlphaComponent(0.36).setStroke()
    let rim=NSBezierPath(roundedRect:NSRect(x:196,y:204,width:632,height:632),xRadius:150,yRadius:150);rim.lineWidth=3;rim.stroke()
    let wave=NSBezierPath();wave.move(to:NSPoint(x:260,y:486));wave.line(to:NSPoint(x:366,y:486));wave.line(to:NSPoint(x:442,y:640));wave.line(to:NSPoint(x:542,y:365));wave.line(to:NSPoint(x:620,y:536));wave.line(to:NSPoint(x:680,y:486));wave.line(to:NSPoint(x:764,y:486));wave.lineWidth=42;wave.lineCapStyle = .round;wave.lineJoinStyle = .round;NSColor.white.setStroke();wave.stroke()
    NSGraphicsContext.restoreGraphicsState()
    let names = size == 1024 ? ["icon_512x512@2x"] : size == 16 ? ["icon_16x16"] : size == 64 ? ["icon_32x32@2x"] : ["icon_\(size)x\(size)","icon_\(size/2)x\(size/2)@2x"]
    for name in names { try bitmap.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:output+"/"+name+".png")) }
}
