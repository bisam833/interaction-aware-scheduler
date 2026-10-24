# Third-party code and components

No third-party source code has been copied or adapted into this repository.

The host provides these external runtime/toolchain components, which are not
project contributions: Linux sched_ext, libbpf, LLVM/Clang, bpftool, the
packaged `scx-scheds` sched_ext binaries, Wayland, Wayland protocols, GNOME,
and libinput. Exact package versions are recorded in
[`docs/environment.md`](environment.md). If source is imported later, record
the upstream URL, commit/version, license, and the exact files adapted here.

Phase 2 uses the include/build scaffold from the locally supplied official
`sched-ext/scx-c-examples` checkout without modifying that checkout. The
project-owned scheduler sources under `src/pulsesched/` are derived work and
are not copied external scheduler binaries.

