import SwiftUI

struct Surface<Content: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            if !title.isEmpty { Label(title,systemImage:symbol).font(.headline).foregroundStyle(.secondary) }
            content
        }.padding(24).frame(maxWidth:.infinity,alignment:.leading)
            .background(Color(nsColor:.controlBackgroundColor),in:.rect(cornerRadius:20))
            .overlay(RoundedRectangle(cornerRadius:20).strokeBorder(.primary.opacity(0.05)))
    }
}
struct DocumentField: View {
    @Bindable var editor: EncounterEditor
    let key: String
    var placeholder = "클릭해서 내용을 작성하세요"
    var disabled = false
    @ViewState private var editing = false
    @FocusState private var focused: Bool
    var body: some View {
        VStack(alignment:.leading,spacing:10) {
            HStack {
                if key.hasPrefix("soap_") { Text(String(key.suffix(1)).uppercased()).font(.system(.headline,design:.rounded)).foregroundStyle(.teal).frame(width:30,height:30).background(.teal.opacity(0.1),in:.rect(cornerRadius:9)) }
                Text(documentTitles[key] ?? key).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                Button(editing ? "편집 마침" : "수정",systemImage:editing ? "checkmark" : "pencil") { editing.toggle(); focused = editing; if !editing { Task { _ = await editor.save() } } }
                    .labelStyle(.iconOnly).buttonStyle(.borderless).help(editing ? "편집 마침" : "\(documentTitles[key] ?? key) 수정").disabled(disabled)
                    .accessibilityIdentifier("edit-\(key)")
            }
            if editing {
                TextEditor(text:Binding(get:{ editor.values[key,default:""] },set:{ editor.edit(key,$0) }))
                    .font(.body).scrollContentBackground(.hidden).frame(minHeight:120,maxHeight:260).focused($focused)
                    .padding(8).background(.quaternary.opacity(0.35),in:.rect(cornerRadius:10)).accessibilityLabel(documentTitles[key] ?? key).accessibilityIdentifier("field-\(key)")
            } else {
                let text = editor.values[key,default:""]
                Text(text.isEmpty ? placeholder : text).font(.system(size:15)).lineSpacing(6).foregroundStyle(text.isEmpty ? .secondary : .primary)
                    .frame(maxWidth:.infinity,alignment:.leading).textSelection(.enabled).onTapGesture { if !disabled { editing = true; focused = true } }
            }
        }.padding(.vertical,4)
    }
}
struct TranscriptView: View {
    @Bindable var editor: EncounterEditor
    @Bindable var model: ClinicModel
    @ViewState private var edit = false
    @ViewState private var follow = true
    var listening: Bool { model.microphone.busy && model.microphone.sid == editor.id }
    var body: some View {
        Surface(title:"음성 전사",symbol:"waveform") {
            HStack(spacing:8) {
                Label(listening ? "실시간 · 이 Mac" : "로컬 음성 인식",systemImage:listening ? "smallcircle.filled.circle" : "lock.shield")
                    .font(.caption).foregroundStyle(listening ? .teal : .secondary)
                Spacer()
                Menu { Button("녹음 파일 가져오기",systemImage:"waveform.badge.plus") { model.importFile(audio:true) }; Button("텍스트 가져오기",systemImage:"doc.badge.plus") { model.importFile(audio:false) } } label: { Image(systemName:"ellipsis") }
                    .menuStyle(.borderlessButton).fixedSize().help("전사 가져오기").disabled(model.importing)
                Button(edit ? "편집 마침" : "전사 수정",systemImage:edit ? "checkmark" : "pencil") { edit.toggle(); if !edit { Task { _ = await editor.save() } } }.labelStyle(.iconOnly).buttonStyle(.borderless).disabled(listening).accessibilityIdentifier("edit-transcript")
            }
            if edit && !listening {
                TextEditor(text:Binding(get:{ editor.values["transcript",default:""] },set:{ editor.edit("transcript",$0) })).font(.system(size:16)).frame(minHeight:240).scrollContentBackground(.hidden).accessibilityLabel("전사 편집")
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment:.leading,spacing:20) {
                            if !(editor.values["transcript"] ?? "").isEmpty { Text(editor.values["transcript",default:""]).textSelection(.enabled) }
                            if listening {
                                Text("\(Text(model.microphone.finalText).foregroundStyle(.primary))\(Text(model.microphone.partial).foregroundStyle(.secondary))").textSelection(.enabled)
                                if model.microphone.text.isEmpty { Label(model.microphone.preparing ? "마이크를 준비하고 있어요" : "듣고 있어요…",systemImage:"waveform").foregroundStyle(.secondary) }
                            } else if editor.values["transcript",default:""].isEmpty {
                                VStack(alignment:.leading,spacing:12) {
                                    Image(systemName:"waveform").font(.system(size:34,weight:.ultraLight)).foregroundStyle(.teal)
                                    Text("대화에 집중하세요").font(.title3.weight(.medium))
                                    Text("마이크를 켜면 말하는 내용이 이곳에 나타납니다.").font(.subheadline).foregroundStyle(.secondary)
                                }.padding(.vertical,30)
                            }
                            Color.clear.frame(height:1).id("tail")
                        }.frame(maxWidth:.infinity,alignment:.leading).font(.system(size:16)).lineSpacing(7)
                    }.frame(minHeight:200,maxHeight:400)
                        .onScrollGeometryChange(for:Bool.self) { g in g.contentOffset.y + g.containerSize.height >= g.contentSize.height-60 } action: { _,atBottom in follow = atBottom }
                        .onChange(of:model.microphone.text) { _,_ in if follow { proxy.scrollTo("tail",anchor:.bottom) } }
                }
            }
            HStack {
                Text(listening ? "회색은 인식 중 · 확정된 문장은 진하게 표시됩니다" : "음성 파일을 만들지 않습니다").font(.caption).foregroundStyle(.tertiary)
                if model.importing { Spacer(); ProgressView().controlSize(.small) }
            }
        }
    }
}
struct SOAPView: View {
    @Bindable var editor: EncounterEditor
    @Bindable var model: ClinicModel
    var body: some View {
        Surface(title:"SOAP 노트",symbol:"note.text") {
            ForEach(["soap_s","soap_o","soap_a","soap_p"],id:\.self) { key in
                DocumentField(editor:editor,key:key,placeholder:"아직 작성된 내용이 없습니다")
                if key != "soap_p" { Divider().opacity(0.5) }
            }
            if ["soap_s","soap_o","soap_a","soap_p"].allSatisfy({editor.values[$0,default:""].isEmpty}), !editor.values["soap",default:""].isEmpty {
                DisclosureGroup("기존 SOAP 원문") { DocumentField(editor:editor,key:"soap") }
            }
            HStack {
                Text("AI 결과는 검토용 초안입니다").font(.caption).foregroundStyle(.tertiary)
                Spacer(); Button("SOAP 복사",systemImage:"doc.on.doc") { model.copy(soap(editor)) }.labelStyle(.iconOnly).buttonStyle(.borderless)
            }
        }
    }
}
struct EncounterView: View {
    @Bindable var editor: EncounterEditor
    @Bindable var model: ClinicModel
    @ViewState private var notes = false
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                header
                if !editor.error.isEmpty { errorPanel }
                if !model.microphone.error.isEmpty || !model.microphone.recovery.isEmpty { microphoneError }
                switch model.stage {
                case .before: before
                case .interview:
                    ViewThatFits(in:.horizontal) {
                        HStack(alignment:.top,spacing:20) { TranscriptView(editor:editor,model:model).frame(minWidth:330); SOAPView(editor:editor,model:model).frame(minWidth:330) }
                        VStack(spacing:20) { TranscriptView(editor:editor,model:model); SOAPView(editor:editor,model:model) }
                    }
                    DisclosureGroup(isExpanded:$notes) { DocumentField(editor:editor,key:"notes") } label: { Label("의사 메모 · 진찰 소견",systemImage:"square.and.pencil").font(.subheadline.weight(.medium)) }.padding(18).background(.background,in:.rect(cornerRadius:16))
                case .review:
                    SOAPView(editor:editor,model:model)
                    Surface(title:"설명과 처방",symbol:"cross.case") { DocumentField(editor:editor,key:"explanation"); Divider(); DocumentField(editor:editor,key:"prescription") }
                    DisclosureGroup("문진 요점 · 참고 의견") { DocumentField(editor:editor,key:"b") }
                case .guide:
                    Surface(title:"환자에게 전할 내용",symbol:"text.bubble") {
                        ForEach(["guide","rx_guide","message"],id:\.self) { key in DocumentField(editor:editor,key:key); HStack { Spacer(); Button("복사",systemImage:"doc.on.doc") { model.copy(editor.values[key,default:""]) }.buttonStyle(.borderless) }; if key != "message" { Divider() } }
                        Button("안내 기록 저장",systemImage:"checkmark.circle") { Task { await model.saveGuidance(editor) } }.buttonStyle(.borderedProminent)
                        Text("로컬 기록만 저장하며 환자에게 발송하지 않습니다.").font(.caption).foregroundStyle(.secondary)
                    }
                    if !editor.values["c",default:""].isEmpty { DisclosureGroup("이전 통합 안내 원문") { DocumentField(editor:editor,key:"c") } }
                }
                if !model.notice.isEmpty { Label(model.notice,systemImage:"checkmark.circle").font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            }.padding(28).frame(maxWidth:1400).frame(maxWidth:.infinity)
        }.background(Color(nsColor:.windowBackgroundColor)).id(editor.id)
    }
    private var header: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack(alignment:.center,spacing:14) {
                Text(String(editor.name.prefix(1))).font(.title2.weight(.medium)).foregroundStyle(.teal).frame(width:54,height:54).background(.teal.opacity(0.09),in:.rect(cornerRadius:18)).accessibilityHidden(true)
                VStack(alignment:.leading,spacing:4) {
                    HStack(alignment:.firstTextBaseline) { Text(editor.name).font(.system(size:28,weight:.semibold)); Text(editor.workspace["survey"]["properties"]["나이"].text).font(.subheadline).foregroundStyle(.secondary) }
                    Text(editor.workspace["survey"]["properties"]["주소증"].text).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer()
                VStack(alignment:.trailing,spacing:7) {
                    Label(editor.status,systemImage:editor.saving ? "arrow.triangle.2.circlepath" : editor.error.isEmpty ? "checkmark.circle" : "exclamationmark.circle").font(.caption).foregroundStyle(editor.error.isEmpty ? Color.secondary : .orange).accessibilityIdentifier("save-state")
                    Menu(editor.record["properties"]["세션 상태"].text) {
                        ForEach(["접수","문진중","한의사검토","안내준비","완료","보류"],id:\.self) { status in Button(status) { Task { await model.setStatus(status,editor:editor) } } }
                    }.menuStyle(.borderlessButton).fixedSize()
                }
            }
            HStack {
                Picker("진료 단계",selection:$model.stage) { ForEach(WorkspaceStage.allCases) { Text($0.rawValue).tag($0) } }.pickerStyle(.segmented).frame(maxWidth:420).accessibilityIdentifier("care-stage")
                Spacer()
                if editor.model != "local" { Label("Apple PCC · 생성할 때 자료 전송",systemImage:"cloud").font(.caption).foregroundStyle(.secondary) }
            }
        }
    }
    private var before: some View {
        VStack(spacing:20) {
            Surface(title:"진료 전 확인",symbol:"person.text.rectangle") {
                Text(editor.workspace["survey"]["properties"]["증상 상세"].text.isEmpty ? "연결된 설문을 확인하세요." : editor.workspace["survey"]["properties"]["증상 상세"].text).font(.body).lineSpacing(6).textSelection(.enabled)
                LabeledContent("복용약",value:editor.workspace["survey"]["properties"]["복용약"].text)
                HStack { Button("설문 수정",systemImage:"pencil") { model.showSurvey = true }; Spacer(); Button("검사 입력",systemImage:"heart.text.clipboard") { model.showExam = true } }.buttonStyle(.borderless)
                if let exam = editor.workspace["exams"].array.first {
                    Divider(); HStack(spacing:30) { metric("혈압",exam["properties"]["수축기혈압"].text+"/"+exam["properties"]["이완기혈압"].text,"mmHg"); metric("맥박",exam["properties"]["맥박"].text,"회/분"); metric("체온",exam["properties"]["체온"].text,"℃") }
                }
            }
            Surface(title:"진료 전 요약",symbol:"sparkles") { DocumentField(editor:editor,key:"a",placeholder:"설문과 검사를 바탕으로 요약을 만들 수 있습니다") }
        }
    }
    private func metric(_ name:String,_ value:String,_ unit:String) -> some View { VStack(alignment:.leading,spacing:4) { Text(name).font(.caption).foregroundStyle(.secondary); HStack(alignment:.firstTextBaseline,spacing:4) { Text(value).font(.title2.weight(.medium)); Text(unit).font(.caption).foregroundStyle(.secondary) } } }
    private var errorPanel: some View {
        VStack(alignment:.leading,spacing:12) {
            Label(editor.error,systemImage:"exclamationmark.circle").foregroundStyle(.orange).textSelection(.enabled)
            if editor.conflicts.isEmpty { Button("저장 재시도") { Task { _ = await editor.save() } } }
            ForEach(editor.conflicts,id:\.self) { key in
                Text(documentTitles[key] ?? key).font(.headline)
                Text("내 입력").font(.caption); Text(editor.values[key,default:""]).textSelection(.enabled)
                Text("저장된 내용").font(.caption); Text(editor.remote["record"]["document"][key].text).textSelection(.enabled)
                HStack { Button("내 입력 유지") { editor.resolve(key,useMine:true) }; Button("저장된 내용 사용") { editor.resolve(key,useMine:false) } }
            }
        }.padding(18).background(.orange.opacity(0.08),in:.rect(cornerRadius:14))
    }
    private var microphoneError: some View {
        VStack(alignment:.leading,spacing:10) {
            Label(model.microphone.error,systemImage:"mic.slash").font(.callout)
            if !model.microphone.recovery.isEmpty {
                Text(model.microphone.recovery).textSelection(.enabled)
                if editor.id != model.microphone.recoverySID {
                    Button("해당 진료 열기") { Task { await model.select(model.microphone.recoverySID) } }
                } else {
                    Button("전사 복구문 가져오기") {
                        editor.edit("transcript",[editor.values["transcript",default:""],model.microphone.recovery].filter{ !$0.isEmpty }.joined(separator:"\n\n"))
                        // The editor's durable draft now owns recovery, including any save conflict.
                        if editor.persist() { model.microphone.clearRecovery() }
                    }
                }
            }
            if model.microphone.recovery.isEmpty { Button("닫기") { model.microphone.error = "" }.buttonStyle(.borderless) }
        }.padding(18).background(.orange.opacity(0.07),in:.rect(cornerRadius:14))
    }
}

#if DEBUG
@MainActor private func previewEncounter() -> some View {
    let model = ClinicModel(config:RuntimeConfig(root:"",database:"",python:"",port:0))
    let workspace = try! JSONDecoder().decode(JSON.self,from:Data(#"{"record":{"id":"preview-only","revision":1,"properties":{"세션 상태":"문진중"},"document":{"transcript":"교육용 합성 자료입니다. 허리 통증이 사흘 전 시작됐습니다. 다리 저림과 발열은 없습니다.","soap_s":"3일 전 시작된 허리 통증. 다리 저림·발열 없음.","soap_o":"체온 36.5°C. 혈압 120/80 mmHg.","soap_a":"진찰 후 평가를 작성합니다.","soap_p":"진찰 결과를 확인하고 계획을 작성합니다."}},"patient":{"name":"김민준 · 미리보기"},"survey":{"properties":{"나이":"만 36세","주소증":"허리 통증"}},"jobs":[],"versions":[],"exams":[]}"#.utf8))
    let editor = EncounterEditor(workspace:workspace,api:model.api)
    return EncounterView(editor:editor,model:model).frame(width:1040,height:760)
}
#Preview("문진 · 라이트") { previewEncounter().preferredColorScheme(.light) }
#Preview("문진 · 다크") { previewEncounter().preferredColorScheme(.dark) }
#endif
