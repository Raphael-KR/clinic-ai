// swift-tools-version: 6.2
import PackageDescription
let package = Package(name: "ClinicMac", platforms: [.macOS("27.0")], products: [.executable(name: "ClinicMac", targets: ["ClinicMac"])], targets: [.executableTarget(name: "ClinicMac", path: "Sources/ClinicMac"), .testTarget(name:"ClinicMacTests",dependencies:["ClinicMac"],path:"Tests")])
