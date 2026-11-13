#!/bin/sh
# Installs kosika-native on an iPhone in iSH (or any 32-bit-capable Linux)
# and runs a first Kosika program's tests. Run with:
#   wget -qO- https://github.com/blackhole8080-darkmatter/kosika/releases/download/phone-preview/try.sh | sh
set -e
BASE=https://github.com/blackhole8080-darkmatter/kosika/releases/download/phone-preview
echo "Kosika: downloading kosika-native (about 8 MB)..."
wget -q -O /usr/local/bin/kosika-native "$BASE/kosika-native-ish"
chmod +x /usr/local/bin/kosika-native
ln -sf /usr/local/bin/kosika-native /usr/local/bin/kos
mkdir -p "$HOME/kosika"
wget -q -O "$HOME/kosika/discrete_maths.kos" "$BASE/discrete_maths.kos"
echo "Kosika: running the tests in discrete_maths.kos..."
cd "$HOME/kosika"
kos test discrete_maths.kos
echo
echo "It works. 'kos' is kosika-native. Try next:"
echo "  kos phone node --relay --name iphone   (lets your other devices reach this phone)"
echo "  kos phone help"
echo "  kos test ~/kosika/discrete_maths.kos"
