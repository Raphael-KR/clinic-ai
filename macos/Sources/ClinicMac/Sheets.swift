import SwiftUI

struct SheetHeader: View {
    let title: String
    @Environment(\.dismiss) private var dismiss
    var body: some View { HStack { Text(title).font(.title2.weight(.semibold)); Spacer(); Button("닫기",systemImage:"xmark") { dismiss() }.labelStyle(.iconOnly).buttonStyle(.borderless).keyboardShortcut(.cancelAction) }.padding(22) }
}
struct ReferenceInspector: View {
    @Bindable var editor: EncounterEditor
    @Bindable var model: ClinicModel
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                HStack { Label("참고 자료",systemImage:"sidebar.right").font(.headline); Spacer(); Button("닫기",systemImage:"xmark") { model.inspector = false }.labelStyle(.iconOnly).buttonStyle(.borderless) }
                Text("원문과 함께 확인하세요").font(.caption).foregroundStyle(.secondary)
                ForEach(["주소증","발병 시점 또는 경과","증상 상세","복용약"],id:\.self) { key in VStack(alignment:.leading,spacing:6) { Text(key).font(.caption.weight(.semibold)).foregroundStyle(.secondary); Text(editor.workspace["survey"]["properties"][key].text.isEmpty ? "미입력" : editor.workspace["survey"]["properties"][key].text).textSelection(.enabled) } }
                Divider(); Text("검사").font(.headline)
                if let exam = editor.workspace["exams"].array.first { ForEach(["수축기혈압","이완기혈압","맥박","체온","HRV","APG","인바디","검사 메모"],id:\.self) { key in LabeledContent(key,value:exam["properties"][key].text.isEmpty ? "미입력" : exam["properties"][key].text) } } else { Text("미검사").foregroundStyle(.secondary) }
                DisclosureGroup("전사 원문 · 시간 정보") {
                    Text(editor.record["document"]["transcript_source"]["text"].text).textSelection(.enabled)
                    ForEach(Array(editor.record["document"]["transcript_source"]["segments"].array.enumerated()),id:\.offset) { _,segment in HStack(alignment:.top) { Text(segment["start"].text).font(.caption.monospacedDigit()).foregroundStyle(.secondary); Text(segment["text"].text).font(.caption).textSelection(.enabled) } }
                }
                DisclosureGroup("이전 전사") { ForEach(Array(editor.record["document"]["transcript_history"].array.enumerated()),id:\.offset) { _,entry in DisclosureGroup(parseDate(entry["replaced_at"].text).formatted(date:.abbreviated,time:.shortened)) { Text(entry["text"].text).textSelection(.enabled) } } }
                Divider(); Text("이 환자의 방문").font(.headline)
                ForEach(editor.workspace["visits"].array,id:\.self) { visit in Button { Task { await model.select(visit["id"].text) } } label: { Label(parseDate(visit["created_at"].text).formatted(date:.abbreviated,time:.shortened),systemImage:visit["id"].text == editor.id ? "checkmark.circle.fill" : "calendar") }.buttonStyle(.borderless) }
            }.font(.subheadline).padding(20)
        }
    }
}
struct CandidateSheet: View {
    @Bindable var editor: EncounterEditor
    @Bindable var model: ClinicModel
    @ViewState private var applying = false
    var body: some View {
        VStack(spacing:0) {
            SheetHeader(title:"AI 초안 비교")
            ScrollView {
                VStack(alignment:.leading,spacing:16) {
                    if editor.workspace["jobs"].array.isEmpty { ContentUnavailableView("아직 생성한 초안이 없습니다",systemImage:"sparkles",description:Text("툴바의 AI 초안 버튼으로 시작하세요.")) }
                    ForEach(editor.workspace["jobs"].array,id:\.recordID) { job in
                        DisclosureGroup {
                            if job["state"].text == "completed" {
                                ForEach(candidateFields(job),id:\.0) { key,text in
                                    VStack(alignment:.leading,spacing:12) {
                                        Text(documentTitles[key] ?? key).font(.headline)
                                        HStack(alignment:.top,spacing:20) {
                                            VStack(alignment:.leading,spacing:8) { Text("현재 기록").font(.caption).foregroundStyle(.secondary); Text(editor.values[key,default:""].isEmpty ? "비어 있음" : editor.values[key,default:""]).textSelection(.enabled) }.frame(maxWidth:.infinity,alignment:.leading)
                                            VStack(alignment:.leading,spacing:8) { Text("새 후보").font(.caption).foregroundStyle(.teal); Text(text).textSelection(.enabled) }.frame(maxWidth:.infinity,alignment:.leading)
                                        }.font(.body).lineSpacing(5)
                                        Button("이 항목 적용") { applying = true; Task { await model.apply(key,text:text,job:job,editor:editor); applying = false } }.disabled(applying).buttonStyle(.bordered)
                                        Divider()
                                    }.padding(.top,12)
                                }
                            } else if job["state"].text == "failed" {
                                Text(job["error"].text).foregroundStyle(.orange).textSelection(.enabled)
                                Button("\(job["provider"]["label"].text)로 재시도") { Task { await model.generate(job["stage"].text,editor:editor,model:job["model"].text,soapStrategy:job["provider"]["soap_strategy"].text == "split" ? "split" : "single") } }
                            } else { ProgressView("초안을 만들고 있어요. 다른 작업을 계속할 수 있습니다.").padding() }
                        } label: {
                            HStack { Image(systemName:"sparkles").foregroundStyle(.teal); VStack(alignment:.leading,spacing:4) { Text((job["stage"].text == "a" ? "진료 전 요약" : job["stage"].text == "b" ? "SOAP 초안" : "환자 안내")+" · "+job["provider"]["label"].text).font(.headline); Text((job["provider"]["soap_strategy"].text == "split" ? "분할 시험 · " : "")+state(job["state"].text)+(job["stale"].flag ? " · 이후 입력이 변경됨" : "")).font(.caption).foregroundStyle(.secondary) }; Spacer(); Text(parseDate(job["created_at"].text),format:.dateTime.hour().minute()).font(.caption).foregroundStyle(.secondary) }
                        }.padding(18).background(.background,in:.rect(cornerRadius:14))
                    }
                    if !editor.error.isEmpty { Text(editor.error).foregroundStyle(.orange) }
                    if !model.notice.isEmpty { Text(model.notice).font(.caption).foregroundStyle(.secondary) }
                }.padding(22)
            }
        }.frame(minWidth:740,idealWidth:880,minHeight:500,idealHeight:680)
    }
    private func state(_ value:String) -> String { switch value { case "queued":"대기 중";case "running":"생성 중";case "completed":"후보 준비";default:"생성 실패" } }
}
struct HistorySheet: View {
    @Bindable var editor: EncounterEditor
    @Bindable var model: ClinicModel
    @ViewState private var selection: String?
    @ViewState private var version: JSON = .null
    var body: some View {
        VStack(spacing:0) {
            SheetHeader(title:"편집 이력")
            HSplitView {
                List(editor.workspace["versions"].array,id:\.self,selection:$selection) { row in VStack(alignment:.leading) { Text(parseDate(row["created_at"].text).formatted(date:.abbreviated,time:.shortened)); Text(row["label"].text).font(.caption).foregroundStyle(.secondary) }.tag(row["id"].text) }.frame(minWidth:200,idealWidth:240)
                ScrollView { VStack(alignment:.leading,spacing:20) {
                    if version == .null { ContentUnavailableView("복구할 이력을 선택하세요",systemImage:"clock.arrow.circlepath") }
                    else { ForEach(documentTitles.keys.sorted(),id:\.self) { key in
                        let text = version["document"][key].text
                        if !text.isEmpty || !editor.values[key,default:""].isEmpty {
                            VStack(alignment:.leading,spacing:10) { Text(documentTitles[key]!).font(.headline); Text(text.isEmpty ? "비어 있음" : text).textSelection(.enabled); Button("이 항목 복구") { editor.edit(key,text); Task { _ = await editor.save(); await model.refresh(editor) } } }.padding(14).background(.background,in:.rect(cornerRadius:12))
                        }
                    }
                } }.padding(18) }.frame(minWidth:380)
            }
        }.frame(width:850,height:640).task { await model.refresh(editor) }.onChange(of:selection) { _,id in version = .null; guard let id else { return }; Task { do { let loaded = try await model.api.request("/api/workspace/\(editor.id)/versions/\(id)"); if selection == id { version = loaded } } catch { model.error = error.localizedDescription } } }
    }
}
struct RecordForm: View {
    @Bindable var model: ClinicModel
    let table: String
    let existing: JSON
    let title: String
    var session: String? = nil
    @Environment(\.dismiss) private var dismiss
    @ViewState private var fields: [String:JSON] = [:]
    @ViewState private var saving = false
    @ViewState private var error = ""
    var schema: [String:JSON] { model.boot["schema"][table]["schema"].object }
    var keys: [String] {
        let priority = ["이름","휴대폰 번호","주민등록번호 앞 6자리","주민등록번호 뒤 7자리","주소증","발병 시점 또는 경과","증상 상세","복용약","검사 기록명","수축기혈압","이완기혈압","맥박","체온"]
        return schema.keys.filter { !["formula","created_time","relation"].contains(schema[$0]?["type"].text ?? "") }.sorted { (priority.firstIndex(of:$0) ?? 100, $0) < (priority.firstIndex(of:$1) ?? 100, $1) }
    }
    var body: some View {
        VStack(spacing:0) {
            SheetHeader(title:title)
            Form { ForEach(keys,id:\.self) { key in field(key) }; if !error.isEmpty { Text(error).foregroundStyle(.orange).textSelection(.enabled) } }.formStyle(.grouped)
            HStack { Text("이 Mac에 저장").font(.caption).foregroundStyle(.secondary); Spacer(); Button("취소",role:.cancel) { dismiss() }; Button(saving ? "저장 중…" : "저장") { Task { await save() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(saving) }.padding(20)
        }.frame(width:620,height:700).onAppear { fields = existing["properties"].object; for key in keys where schema[key]?["type"].text == "multi_select" { fields[key] = .string(existing["properties"][key].array.map(\.text).joined(separator:", ")) }; if let session { fields["연결된 진료 세션"] = .array([.string(session)]) }; if table == "exams", fields["검사 기록명"] == nil { fields["검사 기록명"] = .string("진찰 · "+Date.now.formatted(date:.abbreviated,time:.shortened)) } }
    }
    @ViewBuilder private func field(_ key:String) -> some View {
        let type = schema[key]?["type"].text ?? "text"
        if type == "checkbox" { Toggle(key,isOn:Binding(get:{fields[key]?.flag ?? false},set:{fields[key] = .bool($0)})) }
        else if type == "select" { Picker(key,selection:Binding(get:{fields[key]?.text ?? ""},set:{fields[key] = .string($0)})) { Text("선택 안 함").tag(""); ForEach(schema[key]?["options"].array ?? [],id:\.self) { Text($0["name"].text).tag($0["name"].text) } } }
        else if key == "주민등록번호 뒤 7자리" { SecureField(key,text:binding(key)) }
        else { TextField(key,text:binding(key),axis:type == "text" ? .vertical : .horizontal).lineLimit(type == "text" ? 2...6 : 1...1).accessibilityIdentifier("record-\(key)") }
    }
    private func binding(_ key:String) -> Binding<String> { Binding(get:{fields[key]?.text ?? ""},set:{fields[key] = .string($0)}) }
    private func save() async {
        saving = true; defer { saving = false }
        var properties: [String:JSON] = [:]
        for key in keys {
            let value = fields[key] ?? .null
            switch schema[key]?["type"].text {
            case "number": if value.text.isEmpty { properties[key] = .null } else if let number = Double(value.text),number.isFinite { properties[key] = .number(number) } else { error = "\(key)에 숫자를 입력해 주세요."; return }
            case "checkbox": properties[key] = .bool(value.flag)
            case "select": properties[key] = .string(value.text)
            case "multi_select": properties[key] = .array(value.text.split(separator:",").map { .string($0.trimmingCharacters(in:.whitespacesAndNewlines)) }.filter { !$0.text.isEmpty })
            default: properties[key] = .string(value.text)
            }
        }
        if let session { properties["연결된 진료 세션"] = .array([.string(session)]) }
        var body: [String:JSON] = ["properties":.object(properties)]
        let id = existing["id"].text
        if !id.isEmpty { body["revision"] = existing["revision"] }
        do {
            let result = try await model.api.request("/api/\(table)"+(id.isEmpty ? "" : "/\(id)"),method:id.isEmpty ? "POST" : "PATCH",body:.object(body))
            await model.refreshVisits()
            if table == "surveys", id.isEmpty, let visit = model.visits.first(where:{ $0.data["properties"]["연결된 초진설문"].array.contains(.string(result["id"].text)) }) { await model.select(visit.id); model.stage = .before }
            else if let current = model.current { await model.refresh(current) }
            dismiss()
        } catch { self.error = error.localizedDescription }
    }
}
struct PatientLinkSheet: View {
    @Bindable var model: ClinicModel
    let editor: EncounterEditor
    @ViewState private var patients: [JSON] = []
    @ViewState private var selected = ""
    @ViewState private var error = ""
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment:.leading,spacing:20) { SheetHeader(title:"환자 연결 정정"); Text("현재 방문의 소속 환자만 바꿉니다. 진료 내용은 보존됩니다."); Picker("연결할 환자",selection:$selected) { ForEach(patients,id:\.self) { Text($0["name"].text+" · "+String($0["id"].text.prefix(8))).tag($0["id"].text) } }; Text(error).foregroundStyle(.orange); Button("선택 환자로 연결") { Task { do { _ = try await model.api.request("/api/workspace/\(editor.id)/assign",method:"POST",body:.object(["patient_id":.string(selected),"previous_id":editor.workspace["patient"]["id"]])); await model.refresh(editor); await model.refreshVisits(); dismiss() } catch { self.error = error.localizedDescription } } }.disabled(selected.isEmpty) }.padding(24).frame(width:550).task { do { patients = try await model.api.request("/api/patients").array; selected = editor.workspace["patient"]["id"].text } catch { self.error = error.localizedDescription } }
    }
}
struct LibrarySheet: View {
    @Bindable var model: ClinicModel
    @ViewState private var selection = "docs"
    @ViewState private var rows: [JSON] = []
    @ViewState private var edit: JSON?
    @ViewState private var create = false
    let tables = ["knowledge":"한의학 자료","crm":"환자 안내 기록","templates":"안내 템플릿","settings":"운영 설정"]
    var body: some View {
        VStack(spacing:0) {
            SheetHeader(title:"자료실 · 관리")
            HStack { Picker("자료 종류",selection:$selection) { Text("참조 문서").tag("docs"); ForEach(tables.keys.sorted(),id:\.self) { Text(tables[$0]!).tag($0) } }; Spacer(); if selection != "docs" { Button("새 항목",systemImage:"plus") { create = true } } }.padding(.horizontal,22)
            ScrollView { VStack(alignment:.leading,spacing:16) {
                if selection == "docs" { ForEach(model.boot["docs"].object.keys.sorted(),id:\.self) { key in DisclosureGroup(model.boot["docs"][key]["title"].text) { Text(model.boot["docs"][key]["content"].text).font(.body).textSelection(.enabled) }.padding(16).background(.background,in:.rect(cornerRadius:12)) } }
                else { ForEach(rows,id:\.self) { row in Button { edit = row } label: { VStack(alignment:.leading,spacing:7) { Text(recordTitle(row)).font(.headline); Text(row["properties"].object.values.map(\.text).filter { !$0.isEmpty }.prefix(3).joined(separator:" · ")).font(.caption).foregroundStyle(.secondary).lineLimit(2) }.frame(maxWidth:.infinity,alignment:.leading).padding(16) }.buttonStyle(.plain).background(.background,in:.rect(cornerRadius:12)) }; if rows.isEmpty { ContentUnavailableView("아직 저장된 항목이 없습니다",systemImage:"tray") } }
            }.padding(22) }
        }.frame(width:800,height:680).task(id:selection) { if selection != "docs" { do { rows = try await model.api.request("/api/\(selection)").array } catch { model.error = error.localizedDescription } } }
            .sheet(isPresented:$create,onDismiss:{ Task { if selection != "docs" { rows = (try? await model.api.request("/api/\(selection)").array) ?? rows } } }) { RecordForm(model:model,table:selection,existing:.null,title:"새 "+(tables[selection] ?? "항목")) }
            .sheet(isPresented:Binding(get:{edit != nil},set:{if !$0 {edit = nil}}),onDismiss:{Task { rows = (try? await model.api.request("/api/\(selection)").array) ?? rows }}) { if let edit { RecordForm(model:model,table:selection,existing:edit,title:recordTitle(edit)) } }
    }
    private func recordTitle(_ record:JSON) -> String { for key in ["제목","자료명","항목명","템플릿명","요청명","이름"] { if !record["properties"][key].text.isEmpty {return record["properties"][key].text} }; return String(record["id"].text.prefix(8)) }
}
