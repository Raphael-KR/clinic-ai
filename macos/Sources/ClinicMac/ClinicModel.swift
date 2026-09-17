import SwiftUI
import AppKit
import Observation
import UniformTypeIdentifiers

@MainActor @Observable final class ClinicModel {
    let config: RuntimeConfig
    let api: ClinicAPI
    let microphone: NativeMicrophone
    var connected = false
    var loading = false
    var boot: JSON = .null
    var visits: [Visit] = []
    var selectedID: String?
    var current: EncounterEditor?
    var editors: [String:EncounterEditor] = [:]
    var search = ""
    var filter = "전체"
    var stage: WorkspaceStage = .interview
    var inspector = false
    var showNewPatient = false
    var showExam = false
    var showSurvey = false
    var showCandidates = false
    var showHistory = false
    var showLibrary = false
    var showPatientLink = false
    var error = ""
    var notice = ""
    var importing = false
    var sending = false
    private var ownedService: Process?
    private var monitor: Task<Void,Never>?
    private var selectionVersion = 0
    init(config: RuntimeConfig) { self.config = config; api = ClinicAPI(baseURL: config.baseURL); microphone = NativeMicrophone(scope:String(config.port)) }
    var filtered: [Visit] { visits.filter { (filter == "전체" || $0.group == filter) && (search.isEmpty || ($0.name + $0.complaint).localizedCaseInsensitiveContains(search)) } }
    var pendingJobs: Int { editors.values.reduce(0) { $0 + $1.workspace["jobs"].array.filter { ["queued","running"].contains($0["state"].text) }.count } }
    func connect(startIfNeeded: Bool = true) async {
        guard !loading else { return }; loading = true; defer { loading = false }
        do { boot = try await api.bootstrap() }
        catch {
            guard startIfNeeded else { self.error = error.localizedDescription; return }
            do {
                let process = Process(); process.executableURL = URL(fileURLWithPath:config.python)
                process.arguments = ["-m","clinic_ai.app","--db",config.database,"--port",String(config.port)]
                process.currentDirectoryURL = URL(fileURLWithPath:config.root)
                process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
                try process.run(); ownedService = process
                var ready = false
                for _ in 0..<40 {
                    try await Task.sleep(for:.milliseconds(200))
                    if let result = try? await api.bootstrap() { boot = result; ready = true; break }
                    if !process.isRunning { break }
                }
                guard ready else { throw ClinicError(message:"로컬 서비스를 시작하지 못했습니다. 프로젝트 위치와 Python을 확인해 주세요.") }
            } catch { self.error = error.localizedDescription; return }
        }
        connected = true; error = ""; await refreshVisits()
        if selectedID == nil, let first = visits.first { await select(first.id) }
        startMonitor()
    }
    func refreshVisits() async {
        do { visits = try await api.request("/api/workspace").array.map { Visit(data:$0) } }
        catch { self.error = error.localizedDescription }
    }
    func select(_ id: String?) async {
        selectionVersion += 1; let version = selectionVersion; selectedID = id
        guard let id else { current = nil; return }
        if let existing = editors[id] { current = existing } else { current = nil }
        do {
            let result = try await api.request("/api/workspace/\(id)")
            guard version == selectionVersion else { return }
            if let existing = editors[id] { existing.ingest(result); current = existing }
            else { let editor = EncounterEditor(workspace:result,api:api); editors[id] = editor; current = editor }
        } catch { if version == selectionVersion { self.error = error.localizedDescription } }
    }
    func refresh(_ editor: EncounterEditor) async {
        do { editor.ingest(try await api.request("/api/workspace/\(editor.id)")) }
        catch { self.error = error.localizedDescription }
    }
    private func startMonitor() {
        monitor?.cancel()
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for:.seconds(1))
                guard let self, !Task.isCancelled else { return }
                for editor in self.editors.values where editor.workspace["jobs"].array.contains(where:{ ["queued","running"].contains($0["state"].text) }) { await self.refresh(editor) }
            }
        }
    }
    func generate(_ kind: String, editor: EncounterEditor? = nil, model: String? = nil, soapStrategy: String = "single") async {
        guard let editor = editor ?? current, !sending else { return }; sending = true; defer { sending = false }
        guard await editor.save() else { return }
        do {
            _ = try await api.request("/api/workspace/\(editor.id)/jobs/\(kind)",method:"POST",body:.object(["request_id":.string(UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()),"model":.string(model ?? editor.model),"soap_strategy":.string(soapStrategy)]))
            await refresh(editor); showCandidates = true
        } catch { self.error = error.localizedDescription }
    }
    func apply(_ key: String, text: String, job: JSON, editor: EncounterEditor) async {
        guard await editor.save() else { return }
        editor.values[key] = text; editor.persist()
        if await editor.save(sourceJob:job["id"].text,field:key) { await refresh(editor); notice = "\(documentTitles[key] ?? key) 적용됨 · 이전 기록은 이력에 보존" }
    }
    func startMic() async { guard let current else { return }; stage = .interview; await microphone.start(current,api:api) }
    func importFile(audio: Bool) {
        guard let editor = current, !importing else { return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = audio ? [.audio] : [.plainText]; panel.message = audio ? "녹음 파일을 선택하세요. 원본 파일은 변경하지 않습니다." : "전사 텍스트를 가져옵니다."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if audio, !(editor.values["transcript"] ?? "").isEmpty {
            let alert = NSAlert(); alert.messageText = "기존 전사를 파일 전사로 교체할까요?"; alert.informativeText = "이전 원문은 전사 이력에 남습니다."; alert.addButton(withTitle:"교체"); alert.addButton(withTitle:"취소")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
        }
        importing = true
        Task {
            defer { importing = false }; guard await editor.save() else { return }
            do {
                let size = try url.resourceValues(forKeys:[.fileSizeKey]).fileSize ?? 0
                guard size <= (audio ? 256*1024*1024 : 300000) else { throw ClinicError(message:"가져오기 허용 크기를 넘었습니다.") }
                let data = try Data(contentsOf:url)
                if audio {
                    _ = try await api.request("/api/transcribe/\(editor.id)",method:"POST",bytes:data,headers:["X-Revision":editor.record["revision"].text,"X-Audio-Extension":"."+url.pathExtension.lowercased()]); await refresh(editor)
                } else {
                    guard let text = String(data:data,encoding:.utf8) else { throw ClinicError(message:"UTF-8 텍스트 파일을 선택해 주세요.") }
                    editor.edit("transcript",[editor.values["transcript",default:""],text].filter { !$0.isEmpty }.joined(separator:"\n\n")); _ = await editor.save()
                }
                notice = "전사를 가져왔습니다."; stage = .interview
            } catch { self.error = error.localizedDescription }
        }
    }
    func copySOAP() { guard let current else { return }; copy(soap(current)) }
    func copy(_ text: String) { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text,forType:.string); notice = "복사했습니다." }
    func exportDocument() {
        guard let current else { return }; let panel = NSSavePanel(); panel.allowedContentTypes = [.plainText]; panel.nameFieldStringValue = "진료기록.txt"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try [current.name,current.values["a",default:""],current.values["transcript",default:""],current.values["notes",default:""],soap(current),current.values["guide",default:""],current.values["rx_guide",default:""]].joined(separator:"\n\n").write(to:url,atomically:true,encoding:.utf8) }
        catch { self.error = error.localizedDescription }
    }
    func newVisit() async {
        guard let current, await current.save() else { return }
        do { let result = try await api.request("/api/workspace/\(current.id)/visit",method:"POST",body:.object(["request_id":.string(UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased())])); await refreshVisits(); await select(result["id"].text); stage = .before }
        catch { self.error = error.localizedDescription }
    }
    func saveGuidance(_ editor: EncounterEditor) async {
        guard await editor.save() else { return }
        do { _ = try await api.request("/api/workspace/\(editor.id)/guidance",method:"POST"); notice = "안내 기록 저장됨 · 환자에게 발송하지 않았습니다." }
        catch { self.error = error.localizedDescription }
    }
    func setStatus(_ value: String, editor: EncounterEditor) async {
        guard await editor.save() else { return }
        do { _ = try await api.request("/api/workspace/\(editor.id)",method:"PATCH",body:.object(["properties":.object(["세션 상태":.string(value)]),"base_properties":.object(["세션 상태":editor.record["properties"]["세션 상태"]]) ])); await refresh(editor); await refreshVisits() }
        catch { self.error = error.localizedDescription }
    }
    func saveAll() async -> Bool { for editor in editors.values { if !(await editor.save()) { return false } }; return true }
    func stopOwnedService() { monitor?.cancel(); if let process = ownedService, process.isRunning { process.interrupt() }; ownedService = nil }
}
@MainActor func soap(_ editor: EncounterEditor) -> String { let keys = ["soap_s","soap_o","soap_a","soap_p"]; if keys.allSatisfy({ editor.values[$0,default:""].isEmpty }) { return editor.values["soap",default:""] }; return zip(["S","O","A","P"],keys).map { "\($0): \(editor.values[$1,default:""])" }.joined(separator:"\n\n") }
func candidateFields(_ job: JSON) -> [(String,String)] {
    let result = job["result"]
    if job["stage"].text == "a" { return [("a",result["content"].text)] }
    if job["stage"].text == "c" { return ["guide","rx_guide","message"].map { ($0,result[$0].text) } }
    let text = result["soap"].text
    let regex = try! NSRegularExpression(pattern:"(?m)^([SOAP]):\\s*")
    let ns = text as NSString; let matches = regex.matches(in:text,range:NSRange(location:0,length:ns.length))
    var fields = [("b",result["content"].text)]
    if matches.map({ ns.substring(with:$0.range(at:1)) }) == ["S","O","A","P"] {
        for (index,match) in matches.enumerated() { let start = match.range.location+match.range.length; let end = index+1 < matches.count ? matches[index+1].range.location : ns.length; fields.append(("soap_"+ns.substring(with:match.range(at:1)).lowercased(),ns.substring(with:NSRange(location:start,length:end-start)).trimmingCharacters(in:.whitespacesAndNewlines))) }
    }
    return fields
}
