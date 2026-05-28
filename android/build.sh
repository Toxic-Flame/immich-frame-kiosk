#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ANDROID_JAR=/usr/share/java/com.android.android-23.jar
OUT=build
PKG=ai.alice.immichframekiosk
rm -rf "$OUT" gen obj
mkdir -p "$OUT" gen obj/classes

aapt package -f -m -J gen -M AndroidManifest.xml -S res -I "$ANDROID_JAR"
/usr/lib/jvm/java-17-openjdk-amd64/bin/javac -source 7 -target 7 -bootclasspath "$ANDROID_JAR" -classpath gen -d obj/classes $(find src gen -name '*.java' | sort)
dalvik-exchange --dex --min-sdk-version=21 --output="$OUT/classes.dex" obj/classes

aapt package -f -M AndroidManifest.xml -S res -I "$ANDROID_JAR" -F "$OUT/ImmichFrameKiosk-unsigned.apk"
(cd "$OUT" && zip -q ImmichFrameKiosk-unsigned.apk classes.dex)
zipalign -f 4 "$OUT/ImmichFrameKiosk-unsigned.apk" "$OUT/ImmichFrameKiosk-aligned.apk"

KS="immich-frame-kiosk-example.keystore"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -v -keystore "$KS" -storepass changeit -keypass changeit -alias alice-frameo -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Immich Frame Kiosk,O=AI Generated Example,C=GB" >/dev/null
fi
apksigner sign --ks "$KS" --ks-pass pass:changeit --key-pass pass:changeit --out "$OUT/ImmichFrameKiosk.apk" "$OUT/ImmichFrameKiosk-aligned.apk"
apksigner verify --verbose "$OUT/ImmichFrameKiosk.apk"
ls -lh "$OUT/ImmichFrameKiosk.apk"
