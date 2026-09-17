import Foundation
import FoundationModels

struct Request: Decodable {
    var action: String
    var instructions: String?
    var prompt: String?
    var stage: String?
    var contentDescription: String?
}

@main
struct Bridge {
    static func output(_ value: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) { print(text) }
    }
    static func main() async {
        do {
            let request = try JSONDecoder().decode(Request.self, from: FileHandle.standardInput.readDataToEndOfFile())
            let model = SystemLanguageModel.default
            let info: [String: Any] = ["variant": model.variant.displayName,
                "contextSize": model.contextSize, "availability": String(describing: model.availability)]
            if request.action == "status" { output(info); return }
            guard model.availability == .available else {
                output(["error": "model_unavailable", "message": "이 Mac의 내장 모델을 사용할 수 없습니다."]); return
            }
            let instructions = request.instructions ?? "한국어로 답하세요."
            let prompt = request.prompt ?? ""
            let tokens = try await model.tokenCount(for: Prompt(instructions + "\n" + prompt))
            var properties: [DynamicGenerationSchema.Property] = []
            if request.stage == "b" {
                for (name, description) in [
                    ("subjective", "S 환자 진술만. 증상 시작 시점, 통증 정도, 악화/완화, 위험 신호 음성, 병력·약·알레르기. 지침을 복사하지 않는다."),
                    ("objective", "O 최신 검사 사실만. 혈압 수축기/이완기, 맥박, 체온의 제공된 숫자 모두 포함. 진찰 소견과 미검사·미측정 구분."),
                    ("assessment", "A 대본에 명시된 평가와 진단 미확정 여부. 치료 계획·행정 정보를 넣지 않는다. 근거가 없으면 확인 필요."),
                    ("plan", "P 대본에 실제로 있는 활동·재진·위험 신호 안내와 처방 유무. 새 치료를 창작하지 않는다.")
                ] { properties.append(.init(name: name, description: description, schema: .init(type: String.self))) }
            }
            if request.stage == "c" {
                for (name, description) in [("guide", "환자에게 직접 전달할 진료 안내문"), ("rx_guide", "실제 처방의 복용법·기간·주의사항. 처방 없음이면 해당 없음"), ("message", "복사 가능한 짧은 환자 안내 메시지. 내부 DB 정보나 작업 지시 제외")] {
                    properties.append(.init(name: name, description: description, schema: .init(type: String.self)))
                }
            }
            properties.append(.init(name: "content", description: request.contentDescription ?? "한국어 한의사 검토용 문서 본문. 나 단계는 SOAP를 반복하지 않고 문진 요점·CDSS·근거·한계를 간결히 작성.", schema: .init(type: String.self)))
            let dynamic = DynamicGenerationSchema(name: "ClinicalDraft", properties: properties)
            let schema = try GenerationSchema(root: dynamic, dependencies: [])
            let schemaTokens = try await model.tokenCount(for: schema)
            guard tokens + schemaTokens + 2400 < model.contextSize else {
                output(["error": "context_limit", "message": "입력 자료가 모델의 문맥 한도를 넘습니다. 전사 또는 연결 자료를 줄인 뒤 다시 생성해 주세요.", "inputTokens": tokens]); return
            }
            let session = LanguageModelSession(model: model, instructions: instructions)
            let result = try await session.respond(to: prompt, schema: schema,
                options: GenerationOptions(temperature: 0.2, maximumResponseTokens: 2200))
            var soap = ""
            if request.stage == "b" {
                let sections = try [("S", "subjective"), ("O", "objective"), ("A", "assessment"), ("P", "plan")].map { label, key in
                    "\(label): \(try result.content.value(String.self, forProperty: key))"
                }
                soap = "[한의사 검토용 초안]\n" + sections.joined(separator: "\n\n")
            }
            var response: [String: Any] = ["content": try result.content.value(String.self, forProperty: "content"), "soap": soap,
                    "variant": model.variant.displayName, "inputTokens": tokens]
            if request.stage == "c" {
                for key in ["guide", "rx_guide", "message"] { response[key] = try result.content.value(String.self, forProperty: key) }
            }
            output(response)
        } catch {
            // Do not echo prompts or model debug records into logs.
            output(["error": "generation_failed", "message": "내장 모델이 생성 요청을 완료하지 못했습니다. 입력 길이·모델 사용 가능 상태·콘텐츠 제한을 확인해 주세요.", "errorType": String(describing: type(of: error))])
        }
    }
}
