import SwiftUI
import AppKit

@MainActor final class ClinicAppDelegate: NSObject, NSApplicationDelegate {
    weak var model: ClinicModel?
    private var terminating = false
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating { return .terminateNow }
        guard let model else { return .terminateNow }
        if model.microphone.preparing || model.microphone.stopping { let alert = NSAlert(); alert.messageText = "마이크 준비·종료가 끝난 뒤 앱을 종료해 주세요."; alert.runModal(); return .terminateCancel }
        if model.microphone.active || model.pendingJobs > 0 {
            let alert = NSAlert(); alert.messageText = "진행 중인 작업이 있습니다."
            alert.informativeText = model.microphone.active ? "마이크를 종료하고 전사를 저장한 뒤 앱을 닫습니다." : "앱이 시작한 서비스의 AI 작업은 종료 시 중단될 수 있습니다. 기존 문서는 유지됩니다."
            alert.addButton(withTitle:"저장하고 종료"); alert.addButton(withTitle:"계속 작업")
            guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        }
        Task {
            if model.microphone.active { await model.microphone.stop(save:true) }
            if !model.microphone.recovery.isEmpty { let alert = NSAlert(); alert.messageText = "저장되지 않은 전사가 있습니다."; alert.informativeText = "전사 복구문을 가져온 뒤 종료해 주세요."; alert.runModal(); sender.reply(toApplicationShouldTerminate:false); return }
            guard await model.saveAll() else { let alert = NSAlert(); alert.messageText = "저장하지 못한 편집이 있습니다."; alert.informativeText = "편집 내용을 임시 보관했습니다. 충돌 또는 연결을 확인한 뒤 다시 종료해 주세요."; alert.runModal(); sender.reply(toApplicationShouldTerminate:false); return }
            model.stopOwnedService(); terminating = true; sender.reply(toApplicationShouldTerminate:true)
        }
        return .terminateLater
    }
}
@main struct ClinicApp: App {
    @NSApplicationDelegateAdaptor(ClinicAppDelegate.self) private var delegate
    @ViewState private var model = ClinicModel(config:.current)
    @AppStorage("nativeAppearance") private var appearance = "system"
    var body: some Scene {
        Window("한의원AI로컬구축",id:"clinic") {
            ClinicRootView(model:model).frame(minWidth:960,minHeight:620)
                .preferredColorScheme(appearance == "dark" ? .dark : appearance == "light" ? .light : nil)
                .background(WindowCloseGuard())
                .onAppear { delegate.model = model }
        }.defaultSize(width:1340,height:860).windowToolbarStyle(.unified).windowResizability(.contentMinSize)
            .commands {
                CommandGroup(replacing:.newItem) { Button("새 환자 접수") { model.showNewPatient = true }.keyboardShortcut("n").disabled(!model.connected) }
                CommandGroup(after:.importExport) { Button("녹음 파일 가져오기…") { model.importFile(audio:true) }.keyboardShortcut("o",modifiers:[.command,.shift]).disabled(model.current == nil || model.importing || model.microphone.busy); Button("전사 텍스트 가져오기…") { model.importFile(audio:false) }.keyboardShortcut("o",modifiers:[.command,.option]).disabled(model.current == nil || model.importing || model.microphone.busy) }
                CommandGroup(after:.saveItem) { Button("모두 저장") { Task { _ = await model.saveAll() } }.keyboardShortcut("s"); Button("진료 기록 내보내기…") { model.exportDocument() }.keyboardShortcut("e",modifiers:[.command,.shift]) }
                CommandMenu("진료") {
                    Button("마이크 시작·종료") { Task { if model.microphone.active { await model.microphone.stop(save:true) } else { await model.startMic() } } }.keyboardShortcut("r",modifiers:[.command,.shift]).disabled(model.current == nil || model.microphone.preparing || model.microphone.stopping)
                    Button("SOAP 복사") { model.copySOAP() }.keyboardShortcut("c",modifiers:[.command,.shift])
                    Button("기록 새로고침") { Task { await model.refreshVisits(); if let current = model.current { await model.refresh(current) } } }.keyboardShortcut("r")
                    Divider()
                    ForEach(Array(WorkspaceStage.allCases.enumerated()),id:\.offset) { index,stage in Button(stage.rawValue) { model.stage = stage }.keyboardShortcut(KeyEquivalent(Character(String(index+1))),modifiers:.command) }
                    Divider(); Button("참고 자료") { model.inspector.toggle() }.keyboardShortcut("i",modifiers:[.command,.option]); Button("편집 이력") { model.showHistory = true }; Button("자료실") { model.showLibrary = true }
                }
            }
        Settings {
            Form {
                Section("화면") { Picker("모양",selection:$appearance) { Text("시스템 설정 따르기").tag("system"); Text("라이트").tag("light"); Text("다크").tag("dark") } }
                Section("로컬 실행") { LabeledContent("버전",value:"0.2 · macOS 27"); LabeledContent("처리",value:"이 Mac · 선택 시 Apple PCC"); Text("마이크 음성은 파일로 저장하지 않습니다. 생성 모델은 각 진료에서 선택합니다.").foregroundStyle(.secondary) }
                Section("데이터 위치") { Text(model.config.database).font(.caption).textSelection(.enabled); Text("기존 진료 DB와 같은 데이터를 사용합니다.").font(.caption).foregroundStyle(.secondary) }
            }.formStyle(.grouped).frame(width:500,height:380)
        }
    }
}

// Keep the window visible if termination is cancelled for a save conflict or live audio.
private struct WindowCloseGuard: NSViewRepresentable {
    final class GuardView: NSView {
        let proxy = CloseDelegate()
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let window, window.delegate !== proxy else { return }
            proxy.previous = window.delegate; window.delegate = proxy
        }
    }
    final class CloseDelegate: NSObject, NSWindowDelegate {
        weak var previous: (any NSWindowDelegate)?
        func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
        override func responds(to selector: Selector!) -> Bool { super.responds(to:selector) || (previous?.responds(to:selector) ?? false) }
        override func forwardingTarget(for selector: Selector!) -> Any? { previous }
    }
    func makeNSView(context:Context) -> GuardView { GuardView() }
    func updateNSView(_ view:GuardView,context:Context) {}
}
