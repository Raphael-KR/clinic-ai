#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
app="$PWD/.build/native/한의원AI로컬구축.app"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
# A stable Apple Development identity preserves the app's designated requirement.
# Never silently fall back to ad-hoc signing: that invalidates TCC grants on updates.
identity="${CLINIC_SIGNING_IDENTITY:-}"
if [ -z "$identity" ] && [ -f macos/Signing.local.xcconfig ]; then
  identity="$(sed -n 's/^[[:space:]]*CODE_SIGN_IDENTITY[[:space:]]*=[[:space:]]*//p' macos/Signing.local.xcconfig | sed 's/[[:space:]]*$//' | tail -n 1)"
fi
if [ -z "$identity" ]; then
  echo "Configure macos/Signing.local.xcconfig or CLINIC_SIGNING_IDENTITY with your Apple Development identity." >&2
  exit 1
fi
if ! security find-identity -v -p codesigning | grep -Fq "$identity"; then
  echo "Required Apple Development signing identity is unavailable. Renew/configure the identity; ad-hoc fallback is disabled." >&2
  exit 1
fi
sdk="$(xcrun --sdk macosx --show-sdk-path)"
compiler="$(xcrun --find swiftc)"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
"$compiler" -sdk "$sdk" -target arm64-apple-macos27.0 -swift-version 6 -parse-as-library -O macos/Sources/ClinicMac/*.swift -o "$app/Contents/MacOS/ClinicMac"
cp macos/Resources/Info.plist "$app/Contents/Info.plist"
cp macos/Resources/AppIcon.icns "$app/Contents/Resources/"
xcrun xcstringstool compile macos/Resources/Localizable.xcstrings --output-directory "$app/Contents/Resources"
CLINIC_BUILD_ROOT="$PWD" python3 - <<'PYCONFIG'
import os,json,sys
from pathlib import Path
root=Path(os.environ['CLINIC_BUILD_ROOT'])
port=int(os.environ.get('CLINIC_NATIVE_PORT','8766'))
default=root/'.build/qa-case/clinic.sqlite3'
db=Path(os.environ.get('CLINIC_NATIVE_DB',str(default if default.exists() else root/'data/clinic.sqlite3'))).resolve()
config={'root':str(root),'database':str(db),'python':sys.executable,'port':port}
(root/'.build/native/한의원AI로컬구축.app/Contents/Resources/Runtime.json').write_text(json.dumps(config,ensure_ascii=False))
PYCONFIG
codesign --force --sign "$identity" --options runtime --entitlements macos/Resources/Clinic.entitlements "$app"
codesign --verify --strict "$app"
echo "$app"
