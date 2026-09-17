import AVFoundation
import Observation

// The audio callback converts and copies PCM into memory, never a recording container.
private final class PCMConverter: @unchecked Sendable {
    let converter: AVAudioConverter
    let output: AVAudioFormat
    init(input: AVAudioFormat) throws {
        output = AVAudioFormat(commonFormat:.pcmFormatFloat32,sampleRate:16000,channels:1,interleaved:false)!
        guard let converter = AVAudioConverter(from:input,to:output) else { throw ClinicError(message:"마이크 오디오 형식을 변환할 수 없습니다.") }
        self.converter = converter
    }
    func convert(_ input: AVAudioPCMBuffer) throws -> (Data, Float) {
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength)*16000/input.format.sampleRate)+64)
        let converted = AVAudioPCMBuffer(pcmFormat:output,frameCapacity:capacity)!
        var supplied = false; var error: NSError?
        let status = converter.convert(to:converted,error:&error) { _,state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true; state.pointee = .haveData; return input
        }
        if status == .error { throw error ?? ClinicError(message:"마이크 변환 오류") as NSError }
        guard let pointer = converted.floatChannelData?[0], converted.frameLength > 0 else { return (Data(),0) }
        let count = Int(converted.frameLength); var energy: Float = 0
        for i in 0..<count { energy += pointer[i]*pointer[i] }
        return (Data(bytes:pointer,count:count*4),sqrt(energy/Float(count)))
    }
}
@MainActor @Observable final class NativeMicrophone {
    var active = false
    var preparing = false
    var stopping = false
    var sid = ""
    var name = ""
    var finalText = ""
    var partial = ""
    var level: Float = 0
    var seconds: Double = 0
    var error = ""
    var recovery = ""
    var recoverySID = ""
    private let recoveryURL: URL
    init(scope: String) {
        let folder = FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("ClinicAI/Recovery",isDirectory:true)
        try? FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        recoveryURL = folder.appendingPathComponent(scope+".json")
        if let data = try? Data(contentsOf:recoveryURL), let saved = try? JSONDecoder().decode(JSON.self,from:data) {
            recovery = saved["text"].text; recoverySID = saved["sid"].text
            if !recovery.isEmpty { error = "이전 실행에서 저장하지 못한 전사가 있습니다." }
        }
    }
    func preserveRecovery(_ text: String, sid: String) {
        recovery = text; recoverySID = sid
        guard !text.isEmpty else { return }
        do {
            let data = try JSONEncoder().encode(JSON.object(["text":.string(text),"sid":.string(sid)]))
            try data.write(to:recoveryURL,options:.atomic)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:recoveryURL.path)
        } catch { self.error += " 복구문 임시 저장 실패: "+error.localizedDescription }
    }
    func clearRecovery() { recovery = ""; recoverySID = ""; error = ""; try? FileManager.default.removeItem(at:recoveryURL) }
    private var engine: AVAudioEngine?
    private var tapInstalled = false
    private var jid = ""
    private var api: ClinicAPI?
    private var editor: EncounterEditor?
    private var packet = Data()
    private var sequence = 0
    private var pending = 0
    private var upload: Task<Void,Never>?
    private var polling: Task<Void,Never>?
    private var generation = UUID()
    var busy: Bool { active || preparing || stopping }
    var text: String { finalText + partial }
    func start(_ editor: EncounterEditor, api: ClinicAPI) async {
        guard !busy else { return }; guard recovery.isEmpty else { error = "이전 전사 복구문을 저장한 뒤 다시 시작해 주세요."; return }; preparing = true; error = ""
        guard await editor.save() else { preparing = false; return }
        let run = UUID(); generation = run; self.editor = editor; self.api = api; sid = editor.id; name = editor.name
        finalText = ""; partial = ""; seconds = 0; sequence = 0; packet = Data(); pending = 0; upload = nil
        do {
            guard await AVCaptureDevice.requestAccess(for:.audio) else { throw ClinicError(message:"시스템 설정 → 개인정보 보호 및 보안 → 마이크에서 한의원AI로컬구축을 허용해 주세요.") }
            guard generation == run, preparing else { return }
            let audio = AVAudioEngine(); let input = audio.inputNode
            let format = input.outputFormat(forBus:0)
            guard format.sampleRate > 0, format.channelCount > 0 else { throw ClinicError(message:"연결된 마이크를 찾을 수 없습니다.") }
            let converter = try PCMConverter(input:format)
            let result = try await api.request("/api/live/start/\(editor.id)",method:"POST",body:.object(["revision":editor.record["revision"]]))
            jid = result["id"].text; engine = audio
            try input.__installTap(onBus:0,bufferSize:AVAudioFrameCount(format.sampleRate / 10),format:format,error:()) { @Sendable [weak self] buffer,_ in
                do {
                    let (bytes,volume) = try converter.convert(buffer)
                    Task { @MainActor [weak self] in self?.receive(bytes,volume:volume,run:run) }
                } catch { Task { @MainActor [weak self] in await self?.fail(error.localizedDescription,run:run) } }
            }
            tapInstalled = true; audio.prepare(); try audio.start(); active = true; preparing = false
            polling = Task { [weak self] in
                while !Task.isCancelled {
                    guard let self, self.active, self.generation == run else { return }
                    do {
                        let result = try await api.request("/api/live/\(self.jid)")
                        guard self.generation == run, !self.stopping else { return }
                        if !result["error"].text.isEmpty { throw ClinicError(message:result["error"].text) }
                        self.finalText = result["text"].text; self.partial = result["partial"].text
                    } catch { if !Task.isCancelled { await self.fail(error.localizedDescription,run:run) }; return }
                    try? await Task.sleep(for:.milliseconds(200))
                }
            }
        } catch { preparing = false; await fail(error.localizedDescription,run:run) }
    }
    private func receive(_ bytes: Data, volume: Float, run: UUID) {
        guard generation == run, active, !stopping else { return }
        level = volume; seconds += Double(bytes.count)/64000; packet.append(bytes)
        while packet.count >= 12800 { let data = packet.prefix(12800); packet.removeFirst(12800); send(Data(data),run:run) }
    }
    private func send(_ bytes: Data, run: UUID) {
        guard let api else { return }
        if pending >= 10 { Task { await fail("오디오 처리 지연으로 중단했습니다. 인식된 문장을 복구할 수 있습니다.",run:run) }; return }
        let seq = sequence; sequence += 1; pending += 1; let previous = upload; let jobID = jid
        upload = Task { [weak self] in
            await previous?.value
            guard let self, self.generation == run else { return }
            do { _ = try await api.request("/api/live/\(jobID)/chunk",method:"POST",bytes:bytes,headers:["X-Sequence":String(seq)]); self.pending -= 1 }
            catch { await self.fail(error.localizedDescription,run:run) }
        }
    }
    private func release() { if tapInstalled { engine?.inputNode.removeTap(onBus:0); tapInstalled = false }; engine?.stop(); engine = nil; polling?.cancel(); polling = nil; level = 0 }
    func stop(save: Bool) async {
        guard active, !stopping, let api else { return }; stopping = true
        let run = generation; release()
        if save, !packet.isEmpty { send(packet,run:run) }; packet = Data()
        await upload?.value
        guard generation == run else { stopping = false; return }
        do {
            let result = try await api.request("/api/live/\(jid)/\(save ? "stop" : "cancel")",method:"POST")
            if result["unsaved"].flag { error = result["message"].text; preserveRecovery(result["transcript"]["text"].text,sid:sid) }
            if !result["record"].object.isEmpty, let editor {
                let workspace = try await api.request("/api/workspace/\(sid)"); editor.ingest(workspace)
            }
            if result["empty"].flag { error = result["message"].text }
        } catch { self.error = error.localizedDescription; preserveRecovery(text,sid:sid); _ = try? await api.request("/api/live/\(jid)/cancel",method:"POST") }
        active = false; preparing = false; stopping = false; jid = ""; finalText = ""; partial = ""; generation = UUID()
    }
    private func fail(_ message: String, run: UUID) async {
        guard generation == run else { return }; generation = UUID(); release(); active = false; preparing = false; stopping = false
        error = message; preserveRecovery(text,sid:sid)
        let old = jid; jid = ""
        if !old.isEmpty, let api { _ = try? await api.request("/api/live/\(old)/cancel",method:"POST") }
    }
}
