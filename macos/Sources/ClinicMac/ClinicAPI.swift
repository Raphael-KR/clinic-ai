import Foundation

struct RuntimeConfig: Decodable, Sendable {
    var root: String
    var database: String
    var python: String
    var port: Int
    static var current: RuntimeConfig {
        guard let url = Bundle.main.url(forResource: "Runtime", withExtension: "json"), let data = try? Data(contentsOf: url), let result = try? JSONDecoder().decode(Self.self, from: data) else { fatalError("Runtime.json is missing") }
        return result
    }
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }
}
actor ClinicAPI {
    let baseURL: URL
    private var token = ""
    private let session: URLSession
    init(baseURL: URL) {
        self.baseURL = baseURL
        let c = URLSessionConfiguration.ephemeral; c.timeoutIntervalForRequest = 180; c.timeoutIntervalForResource = 240; c.urlCache = nil
        session = URLSession(configuration: c)
    }
    func bootstrap() async throws -> JSON {
        let value = try await request("/api/bootstrap")
        token = value["csrf"].text
        guard !token.isEmpty else { throw ClinicError(message: "한의원AI로컬구축 서비스에 연결하지 못했습니다.") }
        return value
    }
    func request(_ path: String, method: String = "GET", body: JSON? = nil, bytes: Data? = nil, headers: [String: String] = [:]) async throws -> JSON {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        if method != "GET" {
            request.setValue(token, forHTTPHeaderField: "X-CSRF-Token")
            request.setValue(bytes == nil ? "application/json" : "application/octet-stream", forHTTPHeaderField: "Content-Type")
            request.httpBody = try bytes ?? JSONEncoder().encode(body ?? .object([:]))
        }
        for (k,v) in headers { request.setValue(v, forHTTPHeaderField: k) }
        let (data,response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw ClinicError(message: "로컬 서비스 응답이 없습니다.") }
        let json = (try? JSONDecoder().decode(JSON.self, from: data)) ?? .null
        guard (200..<300).contains(response.statusCode) else { throw ClinicError(message: json["error"].text.isEmpty ? "요청을 처리하지 못했습니다 (\(response.statusCode))." : json["error"].text, status: response.statusCode) }
        return json
    }
}
