#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p .build
/Library/Developer/CommandLineTools/usr/bin/swiftc \
  -sdk /Library/Developer/CommandLineTools/SDKs/MacOSX27.0.sdk \
  -parse-as-library -O -target arm64-apple-macos27.0 \
  clinic_ai/native/SpeechBridge.swift -o .build/speech-bridge
