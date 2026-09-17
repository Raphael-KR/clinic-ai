import SwiftUI
// The installed 27 SDK ships the State macro interface without its CLT plugin.
// Select the public State property-wrapper type explicitly; no SDK modification.
typealias ViewState<Value> = SwiftUI.State<Value>
import Foundation

enum JSON: Codable, Sendable, Equatable, ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByBooleanLiteral {
    case object([String: JSON]), array([JSON]), string(String), number(Double), bool(Bool), null
    init(stringLiteral value: String) { self = .string(value) }
    init(integerLiteral value: Int) { self = .number(Double(value)) }
    init(booleanLiteral value: Bool) { self = .bool(value) }
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode([String: JSON].self) { self = .object(v) }
        else { self = .array(try c.decode([JSON].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self { case .object(let v): try c.encode(v); case .array(let v): try c.encode(v); case .string(let v): try c.encode(v); case .number(let v): try c.encode(v); case .bool(let v): try c.encode(v); case .null: try c.encodeNil() }
    }
    subscript(_ key: String) -> JSON { object[key] ?? .null }
    var object: [String: JSON] { if case .object(let v) = self { return v }; return [:] }
    var array: [JSON] { if case .array(let v) = self { return v }; return [] }
    var text: String { switch self { case .string(let v): return v; case .number(let v): return v == floor(v) ? String(Int(v)) : String(v); case .bool(let v): return v ? "예" : "아니요"; default: return "" } }
    var flag: Bool { if case .bool(let v) = self { return v }; return false }
    var int: Int { if case .number(let v) = self { return Int(v) }; return 0 }
}
struct Visit: Identifiable, Equatable {
    var data: JSON
    var id: String { data["id"].text }
    var name: String { data["patient"]["name"].text }
    var complaint: String { data["patient"]["complaint"].text }
    var age: String { data["patient"]["age"].text }
    var status: String { data["properties"]["세션 상태"].text }
    var group: String { switch status { case "문진중": "진료 중"; case "AI문서작성", "한의사검토", "안내준비": "기록 정리"; case "완료": "완료"; case "보류": "보류"; default: "대기" } }
    var date: Date { parseDate(data["created_at"].text) }
}
func parseDate(_ text: String) -> Date {
    let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f.date(from: text) ?? ISO8601DateFormatter().date(from: text) ?? .distantPast
}
enum WorkspaceStage: String, CaseIterable, Identifiable {
    case before = "진료 전", interview = "문진", review = "기록 검토", guide = "환자 안내"
    var id: Self { self }
    var symbol: String { switch self { case .before: "person.text.rectangle"; case .interview: "waveform"; case .review: "note.text"; case .guide: "paperplane" } }
}
let documentTitles: [String: String] = ["a":"진료 전 요약", "transcript":"전사", "notes":"의사 메모", "soap_s":"환자 진술", "soap_o":"검사와 진찰", "soap_a":"평가", "soap_p":"계획", "b":"문진 요점", "explanation":"설명할 내용", "prescription":"처방 · 복용법", "guide":"환자 안내문", "rx_guide":"처방 설명", "message":"안내 메시지", "soap":"기존 SOAP", "c":"이전 통합 안내"]
struct ClinicError: LocalizedError { var message: String; var status: Int = 0; var errorDescription: String? { message } }

extension JSON { var recordID: String { self["id"].text } }
