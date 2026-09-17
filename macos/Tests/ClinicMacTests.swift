import Testing
import Foundation
@testable import ClinicMac

@Suite(.serialized) struct ClinicMacTests {
    @Test func candidateParserPreservesSections() {
        let fields = candidateFields(["stage":"b","result":["soap":"S: 증상\n\nO: 검사\n\nA: 평가\n\nP: 계획","content":"요점"]])
        #expect(fields.map(\.0) == ["b","soap_s","soap_o","soap_a","soap_p"])
        #expect(fields.last?.1 == "계획")
        let malformed = candidateFields(["stage":"b","result":["soap":"S: 증상\nP: 계획","content":"요점"]])
        #expect(malformed.map(\.0) == ["b"])
    }
    @Test @MainActor func microphoneRecoverySurvivesRelaunch() {
        let scope = "test-"+UUID().uuidString
        let first = NativeMicrophone(scope:scope)
        first.preserveRecovery("합성 복구문",sid:"synthetic")
        let relaunched = NativeMicrophone(scope:scope)
        #expect(relaunched.recovery == "합성 복구문")
        #expect(relaunched.recoverySID == "synthetic")
        relaunched.clearRecovery()
        #expect(NativeMicrophone(scope:scope).recovery.isEmpty)
    }
    @Test func decodingAndDate() throws {
        let raw = Data("{\"number\":36.5,\"flag\":false,\"nested\":{\"a\":\"한글\"}}".utf8)
        let value = try JSONDecoder().decode(JSON.self,from:raw)
        #expect(value["number"].text == "36.5")
        #expect(value["nested"]["a"].text == "한글")
        #expect(try JSONDecoder().decode(JSON.self,from:JSONEncoder().encode(value)) == value)
        #expect(parseDate("2026-09-17T00:00:00.000000+00:00") != .distantPast)
    }
    @Test(.enabled(if: ProcessInfo.processInfo.environment["CLINIC_NATIVE_INTEGRATION"] == "1")) @MainActor func syntheticServiceSavesConflictsAndRecovery() async throws {
        guard ProcessInfo.processInfo.environment["CLINIC_NATIVE_INTEGRATION"] == "1" else { return }
        let api = ClinicAPI(baseURL:URL(string:"http://127.0.0.1:8768")!)
        _ = try await api.bootstrap()
        let rows = try await api.request("/api/workspace").array
        let first = try #require(rows.first)
        // Only the deliberately isolated synthetic service is a valid test target.
        #expect(first["patient"]["name"].text.contains("시험"))
        guard first["patient"]["name"].text.contains("시험") else { return }
        let id = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        _ = try await api.request("/api/workspace/\(first["id"].text)/visit",method:"POST",body:["request_id":.string(id)])
        let initial = try await api.request("/api/workspace/\(id)")
        let editor = EncounterEditor(workspace:initial,api:api)
        editor.values["notes"] = "최초 편집"
        #expect(await editor.save())
        // A write started before the next keystroke must not discard that keystroke.
        editor.values["notes"] = "진행 중 편집"
        let pending = Task { await editor.save() }
        await Task.yield()
        editor.values["notes"] = "마지막 키 입력"
        #expect(await pending.value)
        #expect(await editor.save())
        var read = try await api.request("/api/workspace/\(id)")
        #expect(read["record"]["document"]["notes"].text == "마지막 키 입력")
        // Unrelated remote fields may merge; a same-field conflict must be visible.
        let other = EncounterEditor(workspace:read,api:api)
        other.values["notes"] = "다른 화면의 편집"
        #expect(await other.save())
        editor.values["notes"] = "내 편집 유지"
        #expect(!(await editor.save()))
        #expect(editor.conflicts == ["notes"])
        #expect(editor.values["notes"] == "내 편집 유지")
        editor.resolve("notes",useMine:true)
        #expect(await editor.save())
        read = try await api.request("/api/workspace/\(id)")
        #expect(read["record"]["document"]["notes"].text == "내 편집 유지")
        #expect(!read["versions"].array.isEmpty)
        editor.values["soap_s"] = "종료 전 미저장 편집"; editor.persist()
        let restored = EncounterEditor(workspace:read,api:api)
        #expect(restored.values["soap_s"] == "종료 전 미저장 편집")
        #expect(await restored.save())
        #expect(!FileManager.default.fileExists(atPath:restored.draftURL.path))
        // Source provenance survives through the existing apply/save contract.
        let kim = try #require(rows.first(where:{$0["patient"]["name"].text.contains("김민준")}))
        let kimWork = try await api.request("/api/workspace/\(kim["id"].text)")
        #expect(kimWork["sources"]["a"]["generation"]["id"].text == "local")
    }
}
extension JSON: ExpressibleByDictionaryLiteral {
    public init(dictionaryLiteral elements: (String,JSON)...) { self = .object(Dictionary(uniqueKeysWithValues:elements)) }
}
