import Foundation
import Speech
import AVFoundation
import CoreMedia

@main
struct LiveSpeechBridge {
    static func emit(_ value: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: value),
           var line = String(data: data, encoding: .utf8)?.data(using: .utf8) {
            line.append(10)
            FileHandle.standardOutput.write(line)
        }
    }
    static func readExactly(_ count: Int) throws -> Data? {
        var data = Data()
        while data.count < count {
            guard let part = try FileHandle.standardInput.read(upToCount: count-data.count), !part.isEmpty else {
                if data.isEmpty { return nil }
                throw NSError(domain: "TruncatedPCM", code: 1)
            }
            data.append(part)
        }
        return data
    }
    static func main() async {
        do {
            let locale = Locale(identifier: "ko_KR")
            let installed = await SpeechTranscriber.installedLocales
            guard SpeechTranscriber.isAvailable,
                  installed.contains(where: {$0.identifier.replacingOccurrences(of: "-", with: "_") == "ko_KR"}),
                  let matched = await SpeechTranscriber.supportedLocale(equivalentTo: locale) else {
                emit(["error":"한국어 로컬 전사 엔진을 사용할 수 없습니다."]); return
            }
            let transcriber = SpeechTranscriber(locale: matched, transcriptionOptions: [],
                // Live capture favors responsive provisional text. File transcription retains its own settings.
                reportingOptions: [.volatileResults, .fastResults], attributeOptions: [.audioTimeRange, .transcriptionConfidence])
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            let converter = try await AnalyzerInputConverter.converter(compatibleWith: [transcriber])
            let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
            let (stream, continuation) = AsyncThrowingStream<AnalyzerInput, Error>.makeStream(bufferingPolicy: .bufferingOldest(100))
            let results = Task {
                for try await result in transcriber.results {
                    var segments: [[String: Any]] = []
                    for run in result.text.runs {
                        let range = run.audioTimeRange ?? result.range
                        var segment: [String: Any] = ["text":String(result.text[run.range].characters),
                            "start":CMTimeGetSeconds(range.start), "end":CMTimeGetSeconds(CMTimeRangeGetEnd(range))]
                        if let confidence = run.transcriptionConfidence { segment["confidence"] = confidence }
                        segments.append(segment)
                    }
                    emit(["type":"result", "final":result.isFinal, "text":String(result.text.characters), "segments":segments])
                }
            }
            let reader = Task.detached {
                do {
                    var samples: Int64 = 0
                    while let header = try readExactly(4) {
                        let size = header.withUnsafeBytes { Int(UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self))) }
                        guard size > 0, size <= 64000, size % 4 == 0, let data = try readExactly(size) else {
                            throw NSError(domain: "InvalidPCM", code: 1)
                        }
                        let frames = AVAudioFrameCount(size / 4)
                        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
                        buffer.frameLength = frames
                        data.copyBytes(to: UnsafeMutableRawBufferPointer(start: buffer.floatChannelData![0], count: size))
                        for input in try converter.convert(buffer, at: AVAudioTime(sampleTime: samples, atRate: 16000)) {
                            if case .dropped = continuation.yield(input) { throw NSError(domain: "PCMBackpressure", code: 1) }
                        }
                        samples += Int64(frames)
                    }
                    for input in try converter.flush() {
                        if case .dropped = continuation.yield(input) { throw NSError(domain: "PCMBackpressure", code: 1) }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            emit(["type":"ready", "sampleRate":16000])
            try await analyzer.start(inputSequence: stream)
            await reader.value
            try await analyzer.finalizeAndFinishThroughEndOfInput()
            try await results.value
            emit(["type":"done"])
        } catch {
            emit(["error":"실시간 전사가 중단되었습니다. 화면의 전사문을 확인해 주세요.", "errorType":String(describing:type(of:error))])
        }
    }
}
