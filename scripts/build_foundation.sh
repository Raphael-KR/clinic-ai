#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p .build
# The installed CLT 6.4 compiler matches this Mac's macOS 27 SDK.
/Library/Developer/CommandLineTools/usr/bin/swiftc \
  -sdk /Library/Developer/CommandLineTools/SDKs/MacOSX27.0.sdk \
  -parse-as-library -O -target arm64-apple-macos27.0 \
  clinic_ai/native/FoundationBridge.swift -o .build/foundation-bridge
