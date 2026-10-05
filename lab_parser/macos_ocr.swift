// Local OCR bridge. No network calls or persisted page images.
import Foundation
import PDFKit
import Vision
import AppKit

struct OCRWord: Codable {
    let text: String
    let x0: Double
    let top: Double
    let x1: Double
    let bottom: Double
    let confidence: Float
    let geometry_approximate: Bool
}

struct OCRPage: Codable {
    let width: Double
    let height: Double
    let words: [OCRWord]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

do {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.minimumTextHeight = 0.003
    request.usesLanguageCorrection = true
    let supported = try request.supportedRecognitionLanguages()
    if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--languages" {
        print(String(data: try JSONEncoder().encode(supported), encoding: .utf8)!)
        exit(0)
    }
    guard CommandLine.arguments.count == 3,
          let pageNumber = Int(CommandLine.arguments[2]), pageNumber > 0,
          let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])),
          let page = document.page(at: pageNumber - 1) else { fail("Cannot open PDF page") }
    guard supported.contains("ru-RU"), supported.contains("en-US") else {
        fail("Russian/English OCR languages are unavailable on this macOS version")
    }
    request.recognitionLanguages = ["ru-RU", "en-US"]
    request.customWords = ["Гемоглобин", "Ферритин", "Эритроциты", "Лейкоциты", "Ретикулоциты", "Гематокрит"]
    let bounds = page.bounds(for: .mediaBox)
    guard bounds.width > 0 && bounds.height > 0 else { fail("Invalid page dimensions") }
    let scale = min(300.0 / 72.0, 5000.0 / max(bounds.width, bounds.height))
    let pixelWidth = Int(ceil(bounds.width * scale))
    let pixelHeight = Int(ceil(bounds.height * scale))
    guard let context = CGContext(data: nil, width: pixelWidth, height: pixelHeight,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        fail("Cannot allocate page image")
    }
    context.setFillColor(CGColor(gray: 1, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: pixelWidth, height: pixelHeight))
    context.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: context)
    guard let image = context.makeImage() else { fail("Cannot render PDF page") }
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    var words: [OCRWord] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let text = candidate.string
        let regex = try NSRegularExpression(pattern: "\\S+")
        for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(match.range, in: text) else { continue }
            let box = try candidate.boundingBox(for: range)
            let rect: CGRect
            if let box = box {
                rect = box.boundingBox
            } else {
                // Vision may not expose a word box for punctuation/units.
                // Preserve those tokens with an explicitly approximate box.
                let line = observation.boundingBox
                let total = Double((text as NSString).length)
                rect = CGRect(x: line.minX + line.width * Double(match.range.location) / total,
                              y: line.minY, width: line.width * Double(match.range.length) / total,
                              height: line.height)
            }
            words.append(OCRWord(text: String(text[range]),
                x0: rect.minX * bounds.width, top: (1 - rect.maxY) * bounds.height,
                x1: rect.maxX * bounds.width, bottom: (1 - rect.minY) * bounds.height,
                confidence: candidate.confidence, geometry_approximate: box == nil))
        }
    }
    let result = OCRPage(width: bounds.width, height: bounds.height, words: words)
    print(String(data: try JSONEncoder().encode(result), encoding: .utf8)!)
} catch {
    fail("OCR failed: \(error.localizedDescription)")
}
