set -e
cd "$(dirname "$0")/root/usr/share"
for f in ../share/seabios/*.bin; do cp -fL "$f" qemu/ 2>/dev/null || true; done
cp -fL seabios/*.bin qemu/
ls qemu | grep -E "^(bios|vgabios-stdvga)" 
find . -xtype l | head
