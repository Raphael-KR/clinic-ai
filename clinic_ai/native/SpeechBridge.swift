import Foundation
import Speech
import AVFoundation
import CoreMedia

struct SpeechRequest: Decodable { var action: String; var path: String? }
struct Segment: Codable, Sendable {
    var text: String
    var start: Double
    var end: Double
    var confidence: Double?
}

@main
struct SpeechBridge {
    static func emit(_ value: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) { print(text) }
    }
    static func collect(_ transcriber: SpeechTranscriber) async throws -> [Segment] {
        var segments: [Segment] = []
        for try await result in transcriber.results {
            for run in result.text.runs {
                let text = String(result.text[run.range].characters)
                guard !text.isEmpty else { continue }
                let range = run.audioTimeRange ?? result.range
                let start = CMTimeGetSeconds(range.start)
                let end = CMTimeGetSeconds(CMTimeRangeGetEnd(range))
                guard start.isFinite, end.isFinite, end >= start else { continue }
                segments.append(Segment(text: text, start: start, end: end,
                                        confidence: run.transcriptionConfidence))
            }
        }
        return segments
    }
    static func main() async {
        do {
            let req = try JSONDecoder().decode(SpeechRequest.self, from: FileHandle.standardInput.readDataToEndOfFile())
            let locale = Locale(identifier: "ko_KR")
            let installed = await SpeechTranscriber.installedLocales.map(\.identifier)
            let supported = await SpeechTranscriber.supportedLocale(equivalentTo: locale)
            if req.action == "status" {
                emit(["available": SpeechTranscriber.isAvailable,
                      "koreanSupported": supported != nil, "installedLocales": installed,
                      "engine": "Apple SpeechTranscriber", "locale": "ko_KR"]); return
            }
            guard SpeechTranscriber.isAvailable, let matched = supported,
                  installed.contains(where: { $0.replacingOccurrences(of: "-", with: "_") == "ko_KR" }) else {
                emit(["error":"unavailable", "message":"한국어 전사 모델을 사용할 수 없습니다. 설치된 모델 상태를 확인해 주세요."]); return
            }
            guard let path = req.path else { emit(["error":"invalid_file", "message":"오디오 파일이 필요합니다."]); return }
            let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
            let duration = Double(file.length) / file.processingFormat.sampleRate
            guard duration.isFinite, duration > 0, duration <= 7200 else {
                emit(["error":"duration_limit", "message":"0초 초과 2시간 이하의 녹음 파일을 사용해 주세요."]); return
            }
            let transcriber = SpeechTranscriber(locale: matched, transcriptionOptions: [], reportingOptions: [],
                                                attributeOptions: [.audioTimeRange, .transcriptionConfidence])
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            async let segments = collect(transcriber)
            if let end = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: end)
            } else {
                try await analyzer.finalizeAndFinishThroughEndOfInput()
            }
            let result = try await segments
            let text = result.map(\.text).joined()
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                emit(["error":"no_speech", "message":"인식된 발화가 없습니다. 녹음을 확인해 주세요."]); return
            }
            let encoded = try JSONEncoder().encode(result)
            emit(["text":text, "segments":try JSONSerialization.jsonObject(with: encoded),
                  "duration":duration, "locale":"ko_KR", "engine":"Apple SpeechTranscriber"])
        } catch {
            emit(["error":"transcription_failed", "message":"녹음 파일을 전사하지 못했습니다. 지원되는 오디오 형식과 엔진 상태를 확인해 주세요.",
                  "errorType":String(describing:type(of:error))])
        }
    }
}
