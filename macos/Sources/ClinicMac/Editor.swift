import Foundation
import Observation

@MainActor @Observable final class EncounterEditor: Identifiable {
    let id: String
    let api: ClinicAPI
    var workspace: JSON
    var values: [String: String]
    var base: [String: String]
    var status = "저장됨"
    var error = ""
    var conflicts: [String] = []
    var remote: JSON = .null
    var saving = false
    var model = "local"
    private var pending: Task<Void, Never>?
    private var savingTask: Task<Bool, Never>?
    var record: JSON { workspace["record"] }
    var name: String { workspace["patient"]["name"].text }
    var dirty: Bool { values.contains { $0.value != base[$0.key, default: ""] } }
    var draftURL: URL {
        let folder = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("ClinicAI/Drafts", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        // Separate drafts for independent local test/production services.
        return folder.appendingPathComponent("\(api.baseURL.port ?? 0)-\(id).json")
    }
    init(workspace: JSON, api: ClinicAPI) {
        self.workspace = workspace; self.api = api; id = workspace["record"]["id"].text
        let initial = workspace["record"]["document"].object.mapValues(\.text); base = initial; values = initial
        if let data = try? Data(contentsOf: draftURL), let draft = try? JSONDecoder().decode(JSON.self, from: data), !draft["changes"].object.isEmpty {
            for (key,value) in draft["changes"].object { values[key] = value.text; base[key] = draft["base"][key].text }
            status = "미저장 편집 복구됨"; error = "이전 편집이 복구됐습니다. 저장하면 최신 기록과 대조합니다."
        }
    }
    func edit(_ key: String, _ value: String) { values[key] = value; status = "저장 대기"; persist(); pending?.cancel(); pending = Task { try? await Task.sleep(for: .milliseconds(650)); guard !Task.isCancelled else { return }; _ = await save() } }
    @discardableResult func persist() -> Bool {
        if !dirty { try? FileManager.default.removeItem(at: draftURL); return true }
        let changes = values.filter { $0.value != base[$0.key,default: ""] }
        let json = JSON.object(["changes":.object(changes.mapValues(JSON.string)),"base":.object(base.mapValues(JSON.string))])
        if let data = try? JSONEncoder().encode(json) { do { try data.write(to: draftURL, options: .atomic); try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:draftURL.path); return true } catch { self.error = "임시 편집을 보관하지 못했습니다: \(error.localizedDescription)" } }; return false
    }
    func ingest(_ latest: JSON) {
        let doc = latest["record"]["document"].object.mapValues(\.text)
        for (k,v) in doc where values[k,default: ""] == base[k,default: ""] { values[k] = v; base[k] = v }
        workspace = latest; persist()
    }
    func save(sourceJob: String? = nil, field: String? = nil) async -> Bool {
        if let task = savingTask { let ok = await task.value; if !ok { return false }; return await save(sourceJob: sourceJob, field: field) }
        guard conflicts.isEmpty else { return false }
        let changes = values.filter { $0.value != base[$0.key,default: ""] }
        guard !changes.isEmpty else { return true }
        let sentBase = Dictionary(uniqueKeysWithValues:changes.keys.map { ($0,JSON.string(base[$0,default: ""])) })
        var payload: [String: JSON] = ["changes":.object(changes.mapValues(JSON.string)),"base":.object(sentBase)]
        if let sourceJob, let field { payload["source_job"] = .string(sourceJob); payload["source_field"] = .string(field) }
        saving = true; status = "저장 중"; error = ""
        let task = Task { () -> Bool in
            defer { self.savingTask = nil; self.saving = false }
            do {
                let result = try await api.request("/api/workspace/\(id)",method:"PATCH",body:.object(payload))
                let doc = result["document"].object.mapValues(\.text)
                for (k,sent) in changes { base[k] = doc[k,default: ""]; if values[k] == sent { values[k] = base[k] } }
                var w = workspace.object; w["record"] = result; workspace = .object(w)
                status = "저장됨"; persist(); return true
            } catch {
                self.error = error.localizedDescription; status = "저장 확인 필요"
                if (error as? ClinicError)?.status == 409, let latest = try? await api.request("/api/workspace/\(id)") {
                    remote = latest
                    conflicts = changes.keys.filter { latest["record"]["document"][$0].text != sentBase[$0]?.text && latest["record"]["document"][$0].text != changes[$0] }.sorted()
                    if conflicts.isEmpty { ingest(latest) }
                }
                persist(); return false
            }
        }
        savingTask = task
        let ok = await task.value
        if ok && dirty { return await save() }; return ok
    }
    func resolve(_ key: String, useMine: Bool) {
        base[key] = remote["record"]["document"][key].text
        if !useMine { values[key] = base[key] }
        conflicts.removeAll { $0 == key }; persist()
        if conflicts.isEmpty { Task { _ = await save() } }
    }
}
