The hard test's workspace: lessons/N.md (each stage and its screen test), tools/screen.py
(boots an image in QEMU and prints its screen), and toolchain/*.sh, which download nasm and QEMU
from Ubuntu (apt-get download, no sudo) and unpack them into toolchain/root.
The tutorial READMEs (BSD licence) are copied in from github.com/dipanshujha/os-build-tutorial.
run.py reads the workspace from OSBUILD_WORKSPACE, by default Aryan_Private/kosika-bench/osbuild.

examples/ holds the tutorial's own code for each lesson, one LESSON.txt each (BSD licence), made from the
repository; the builder is shown it as a worked example after a stage fails twice.
