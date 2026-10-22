set -e
T="$(dirname "$0")"
cd "$T"
rm -rf root && mkdir root
for d in debs/*.deb; do dpkg -x "$d" root; done
echo "nasm: $(root/usr/bin/nasm -v)"
echo "--- libraries qemu is missing:"
ldd root/usr/bin/qemu-system-i386 | grep "not found" || echo none
