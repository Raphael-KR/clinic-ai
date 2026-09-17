import SwiftUI

struct ClinicRootView: View {
    @Bindable var model: ClinicModel
    @ViewState private var columns: NavigationSplitViewVisibility = .all
    var body: some View {
        NavigationSplitView(columnVisibility:$columns) {
            sidebar.navigationSplitViewColumnWidth(min:220,ideal:260,max:340)
        } detail: {
            Group {
                if let editor = model.current { EncounterView(editor:editor,model:model) }
                else if model.loading { ProgressView("한의원AI로컬구축을 준비하고 있어요").frame(maxWidth:.infinity,maxHeight:.infinity) }
                else { ContentUnavailableView { Label("당신의 진료에 집중하세요",systemImage:"waveform.path.ecg") } description: { Text("환자를 선택하면 문진부터 기록, 안내까지\n한 공간에서 이어집니다.") } actions: { Button(model.connected ? "새 환자 접수" : "로컬 서비스 연결") { if model.connected { model.showNewPatient = true } else { Task { await model.connect() } } }.buttonStyle(.borderedProminent) } }
            }.navigationTitle(model.current?.name ?? "한의원AI로컬구축")
                .inspector(isPresented:$model.inspector) { if let editor = model.current { ReferenceInspector(editor:editor,model:model).inspectorColumnWidth(min:270,ideal:300,max:380) } }
                .safeAreaInset(edge:.bottom) { if model.microphone.busy { LiveActivityBar(model:model).padding(.horizontal,20).padding(.bottom,14) } }
                .toolbar { toolbar }
        }.navigationSplitViewStyle(.balanced).tint(.teal)
            .sheet(isPresented:$model.showNewPatient) { RecordForm(model:model,table:"surveys",existing:.null,title:"새 환자 접수") }
            .sheet(isPresented:$model.showExam) { if let editor = model.current { RecordForm(model:model,table:"exams",existing:editor.workspace["exams"].array.first ?? .null,title:"검사 입력",session:editor.id) } }
            .sheet(isPresented:$model.showSurvey) { if let editor = model.current { RecordForm(model:model,table:"surveys",existing:editor.workspace["survey"],title:"설문 수정") } }
            .sheet(isPresented:$model.showCandidates) { if let editor = model.current { CandidateSheet(editor:editor,model:model) } }
            .sheet(isPresented:$model.showHistory) { if let editor = model.current { HistorySheet(editor:editor,model:model) } }
            .sheet(isPresented:$model.showLibrary) { LibrarySheet(model:model) }
            .sheet(isPresented:$model.showPatientLink) { if let editor = model.current { PatientLinkSheet(model:model,editor:editor) } }
            .alert("작업을 확인해 주세요",isPresented:Binding(get:{ !model.error.isEmpty },set:{ if !$0 { model.error = "" } })) { Button("확인",role:.cancel) {} } message: { Text(model.error) }
            .task { await model.connect() }
    }
    private var sidebar: some View {
        VStack(spacing:0) {
            HStack { VStack(alignment:.leading,spacing:4) { Text("한의원AI로컬구축").font(.title2.weight(.semibold)); Text(Date.now,format:.dateTime.month().day().weekday()).font(.caption).foregroundStyle(.secondary) }; Spacer(); Button("새 환자 접수",systemImage:"plus") { model.showNewPatient = true }.labelStyle(.iconOnly).buttonStyle(.borderless).help("새 환자 접수 ⌘N") }.padding(18)
            Picker("진료 상태",selection:$model.filter) { ForEach(["전체","대기","진료 중","기록 정리","완료","보류"],id:\.self) { Text($0).tag($0) } }.padding(.horizontal,14).padding(.bottom,8)
            List(selection:Binding(get:{ model.selectedID },set:{ id in Task { await model.select(id) } })) {
                ForEach(["대기","진료 중","기록 정리","완료","보류"],id:\.self) { group in
                    let rows = model.filtered.filter { $0.group == group }
                    if !rows.isEmpty { Section("\(group) · \(rows.count)") {
                        ForEach(rows) { row in PatientRow(visit:row,recording:model.microphone.busy && model.microphone.sid == row.id).tag(row.id) }
                    } }
                }
            }.listStyle(.sidebar).searchable(text:$model.search,placement:.sidebar,prompt:"환자 이름 또는 주소증")
            Divider().padding(.horizontal,14)
            HStack { Label(model.connected ? "이 Mac에 저장" : "연결 대기",systemImage:"internaldrive").font(.caption).foregroundStyle(.secondary); Spacer(); Button("자료실",systemImage:"books.vertical") { model.showLibrary = true }.labelStyle(.iconOnly).buttonStyle(.borderless).help("자료실 · 관리") }.padding(18)
        }
    }
    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement:.automatic) {
            Button("새로고침",systemImage:"arrow.clockwise") { Task { await model.refreshVisits(); if let current = model.current { await model.refresh(current) } } }.help("기록 새로고침 ⌘R")
        }.visibilityPriority(.low)
        ToolbarItem(placement:.primaryAction) {
            if let editor = model.current {
                Menu {
                    Picker("생성 모델",selection:Binding(get:{editor.model},set:{editor.model = $0})) { ForEach(model.boot["models"].array,id:\.self) { Text($0["label"].text).tag($0["id"].text) } }
                    Divider(); Text(editor.model == "local" ? "이 Mac에서 처리" : "생성하면 진료 텍스트를 Apple PCC로 전송")
                } label: { Label(editor.model == "local" ? "로컬" : editor.model == "cloud" ? "Cloud" : "Cloud Pro",systemImage:editor.model == "local" ? "desktopcomputer" : "cloud") }.help("생성 모델 선택")
            }
        }.visibilityPriority(.low)
        ToolbarItem(placement:.primaryAction) {
            Button {
                Task { if model.microphone.active { await model.microphone.stop(save:true) } else { await model.startMic() } }
            } label: { Label(model.microphone.active ? "종료" : "마이크",systemImage:model.microphone.active ? "stop.fill" : "mic") }
                .disabled(model.current == nil || model.microphone.preparing || model.microphone.stopping).tint(model.microphone.active ? .red : .teal).keyboardShortcut("r",modifiers:[.command,.shift]).help("마이크 시작·종료 ⇧⌘R")
        }.visibilityPriority(.init(higherThan:.high))
        ToolbarSpacer(.fixed,placement:.primaryAction)
        ToolbarItem(placement:.primaryAction) {
            Menu {
                Button("기존 방식으로 생성") { Task { await model.generate(model.stage == .before ? "a" : model.stage == .guide ? "c" : "b") } }
                if model.stage == .interview {
                    Button("SOAP 분할 생성 · 시험") { Task { await model.generate("b", soapStrategy:"split") } }
                }
            } label: { Label("AI 초안",systemImage:"sparkles") } primaryAction: {
                Task { await model.generate(model.stage == .before ? "a" : model.stage == .guide ? "c" : "b") }
            }.disabled(model.current == nil || model.sending).help("현재 작업의 AI 초안 만들기")
        }.visibilityPriority(.high)
        ToolbarItem(placement:.primaryAction) {
            Button("AI 후보",systemImage:model.pendingJobs > 0 ? "hourglass" : "square.stack") { model.showCandidates = true }.disabled(model.current == nil).help(model.pendingJobs > 0 ? "AI 생성 중 · 후보 확인" : "생성 후보 비교")
        }.visibilityPriority(.low)
        ToolbarItem(placement:.primaryAction) {
            Menu {
                Button("검사 입력",systemImage:"heart.text.clipboard") { model.showExam = true }
                Button("편집 이력",systemImage:"clock.arrow.circlepath") { model.showHistory = true }
                Button("이 환자 새 방문",systemImage:"calendar.badge.plus") { Task { await model.newVisit() } }
                Button("환자 연결 정정",systemImage:"person.crop.circle.badge.checkmark") { model.showPatientLink = true }
                Divider(); Button("SOAP 복사",systemImage:"doc.on.doc") { model.copySOAP() }
                Button("진료 기록 내보내기",systemImage:"square.and.arrow.up") { model.exportDocument() }
            } label: { Label("진료 작업",systemImage:"ellipsis") }.disabled(model.current == nil)
        }.visibilityPriority(.low)
        ToolbarItem(placement:.primaryAction) { Button("참고 자료",systemImage:"sidebar.right") { model.inspector.toggle() }.disabled(model.current == nil).help("참고 자료 열기 ⌥⌘I") }
    }
}
struct PatientRow: View {
    let visit: Visit
    var recording: Bool
    var body: some View {
        VStack(alignment:.leading,spacing:8) {
            HStack { Text(visit.name).font(.system(size:15,weight:.semibold)); Spacer(); if recording { Image(systemName:"waveform").foregroundStyle(.red).accessibilityLabel("마이크 사용 중") }; Text(visit.date,format:.dateTime.hour().minute()).font(.caption).foregroundStyle(.secondary) }
            Text(visit.complaint.isEmpty ? "주소증 미입력" : visit.complaint).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            Text(visit.age).font(.caption2).foregroundStyle(.tertiary)
        }.padding(.vertical,9).accessibilityElement(children:.combine)
    }
}
struct LiveActivityBar: View {
    @Bindable var model: ClinicModel
    var body: some View {
        GlassEffectContainer {
            HStack(spacing:16) {
                Canvas { context,size in
                    for index in 0..<16 {
                        let height = 3 + min(CGFloat(model.microphone.level)*15,1)*(12+14*abs(sin(Double(index)*1.7)))
                        let rect = CGRect(x:CGFloat(index)*4,y:(size.height-height)/2,width:2.5,height:height)
                        context.fill(Path(roundedRect:rect,cornerRadius:2),with:.color(.teal))
                    }
                }.frame(width:64,height:30).accessibilityLabel("마이크 입력 음량").accessibilityValue(model.microphone.level > 0.006 ? "소리 입력 중" : "입력 대기")
                VStack(alignment:.leading,spacing:3) { Text(model.microphone.name).font(.subheadline.weight(.semibold)); Text(model.microphone.preparing ? "마이크 준비 중" : model.microphone.stopping ? "전사 저장 중" : "듣고 있어요 · 음성 파일 저장 안 함").font(.caption).foregroundStyle(.secondary) }
                Spacer(minLength:10)
                Text(Duration.seconds(model.microphone.seconds).formatted(.time(pattern:.minuteSecond))).monospacedDigit().font(.subheadline)
                Button("종료",systemImage:"stop.fill") { Task { await model.microphone.stop(save:true) } }.buttonStyle(.glassProminent).tint(.teal).disabled(!model.microphone.active || model.microphone.stopping)
                Menu { Button("전사 중인 진료 열기") { Task { await model.select(model.microphone.sid); model.stage = .interview } }; Button("이번 전사 버리기",role:.destructive) { Task { await model.microphone.stop(save:false) } } } label: { Image(systemName:"ellipsis") }.menuStyle(.borderlessButton).fixedSize().disabled(!model.microphone.active || model.microphone.stopping)
            }.padding(.horizontal,20).padding(.vertical,12).glassEffect(.regular,in:.capsule)
        }.frame(maxWidth:750)
    }
}
extension JSON: Hashable {
    func hash(into hasher: inout Hasher) { switch self { case .object(let v): hasher.combine(0); for k in v.keys.sorted() { hasher.combine(k); hasher.combine(v[k]) }; case .array(let v):hasher.combine(1);hasher.combine(v);case .string(let v):hasher.combine(2);hasher.combine(v);case .number(let v):hasher.combine(3);hasher.combine(v);case .bool(let v):hasher.combine(4);hasher.combine(v);case .null:hasher.combine(5) } }
}
