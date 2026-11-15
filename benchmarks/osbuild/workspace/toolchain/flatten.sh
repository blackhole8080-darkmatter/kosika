cd "$(dirname "$0")/root"
find . -type l | while read -r l; do
  t="$(readlink -f "$l")"
  if [ -f "$t" ]; then cp --remove-destination "$t" "$l"; else rm -rf "$l"; fi
done
echo "links left: $(find . -type l | wc -l)"
rm -rf usr/share/doc usr/share/man
