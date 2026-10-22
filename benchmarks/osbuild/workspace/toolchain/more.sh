set -e
T="$(dirname "$0")"; cd "$T/debs"
apt-get download libjpeg-turbo8 libjpeg8 libpmem1 libfdt1 libnuma1 librdmacm1t64 libibverbs1 libslirp0 libaio1t64 liburing2 libndctl6 libdaxctl1 libnl-3-200 libnl-route-3-200 2>&1 | grep -v "^Get" | tail -5 || true
cd ..
for d in debs/*.deb; do dpkg -x "$d" root; done
export LD_LIBRARY_PATH="$PWD/root/usr/lib/x86_64-linux-gnu"
echo "--- still missing:"
ldd root/usr/bin/qemu-system-i386 | grep "not found" || echo none
root/usr/bin/qemu-system-i386 --version | head -1
