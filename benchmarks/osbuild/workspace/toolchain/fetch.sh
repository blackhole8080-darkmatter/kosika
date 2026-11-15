set -e
cd "$(dirname "$0")/debs"
apt-get download nasm qemu-system-x86 qemu-system-data qemu-system-common seabios ipxe-qemu 2>&1 | tail -3
ls -la
