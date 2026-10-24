# Phase 1B source and partial-mode audit

## Scope

No PulseSched policy code was written. No source scheduler was modified, built,
or loaded during this phase.

## C-example provenance

The official C-example checkout is:

- remote: `https://github.com/sched-ext/scx-c-examples.git`
- branch: `main`
- commit: `370b1cea95c6bb668d5b9261f069d433142b9986`
- commit date: `2026-08-31T05:52:51-10:00`
- source files: GPL-2.0

The selected unchanged baseline is `scheds/c/scx_simple.c` plus
`scheds/c/scx_simple.bpf.c`. SHA-256 values at build time:

```text
fb98f3246faa390faff9e036cce12b86384afeca3cfd1f8fa270812208bd4bfa  scheds/c/scx_simple.c
f8b2d3ab08a326b09e3e7c8a6eeea43991a5b5265ff9bfb4444bf459e36e3f59  scheds/c/scx_simple.bpf.c
46cb4d69e990f8988e7c0425ff9e7bf1cbba5fac84f41ed928ca46669740d0b5  Makefile
```

The repository's bundled x86 BTF header is named
`vmlinux-v6.18-rc4-g6146a0f1dfae.h`; the running host kernel is
`7.2.9-2-cachyos`. This is an explicit source/header provenance mismatch. The
unchanged build nevertheless completed successfully with the host compiler and
libbpf.

## Unchanged build

Command:

```text
make scx_simple
```

Observed result: exit code `0`. The build compiled the BPF object with Clang
23.1, generated the skeleton with bpftool 7.8.0, and linked the userspace
program with `cc`, libbpf, libelf, zlib, zstd, and pthread. No compiler,
libbpf, or verifier API mismatch was emitted during compilation.

The unchanged `scx_qmap` target was also built successfully because its
official `-p` option sets `SCX_OPS_SWITCH_PARTIAL`; it is reserved for the
partial-mode test and is not the selected minimal baseline.

## Official source target

The preferred target is the minimal official Linux `tools/sched_ext/scx_simple`
example. The official Linux documentation describes it as a minimal example;
the upstream C source uses `struct sched_ext_ops` through `SCX_OPS_DEFINE`, with
`select_cpu`, `enqueue`, `dispatch`, `running`, `stopping`, `enable`, `init`,
and `exit` callbacks. The source currently available online is not silently
assumed to match this host's kernel.

The dedicated official `sched-ext/scx-c-examples` repository is also an example
source, but it is not a substitute for a kernel-matched source commit without
recording and verifying that commit against this kernel.

## Local source evidence

The supplied `external/scx` checkout is official, but it is the Rust scheduler
repository, not the C-example repository:

- remote: `https://github.com/sched-ext/scx.git`
- branch: `main`
- commit: `fead94e15184e957469b346b129f412641691bbe`
- commit date: `2026-10-12T11:40:33Z`

Its own README points C examples to a separate repository. The checkout has no
`scx_simple`, `scx_qmap`, `scx_central`, or `tools/sched_ext` paths. Running
`make -C external/scx scx_simple` produced:

```text
make: *** No rule to make target 'scx_simple'.  Stop.
```

- Kernel: `7.2.9-2-cachyos`.
- `/usr/lib/modules/7.2.9-2-cachyos/build/tools/sched_ext/` contains only
  `Kconfig`.
- The build tree has `vmlinux.h` and sched_ext headers, but not the C example
  sources or their build system.
- `scx_simple`, `scx_qmap`, and `scx_central` are not installed.
- The pacman cache contains the binary `scx-scheds` package, not its source.
- `git ls-remote https://github.com/torvalds/linux.git ...` failed with
  `Could not resolve host: github.com`.

## API evidence

The installed kernel headers and BTF expose:

- `SCHED_EXT=7`
- `SCX_OPS_SWITCH_PARTIAL = 8ULL`
- `struct sched_ext_ops`
- the expected core callback fields, including `select_cpu`, `enqueue`,
  `dispatch`, `running`, `stopping`, `enable`, `init`, and `exit`.

This proves the flag exists in the kernel API surface, not that an example
compiled against another source revision will load unchanged.

## Partial scheduling result

PASSED at the basic runtime criterion using the unchanged official
`scx_qmap -p` binary. Observed evidence:

```text
state=enabled
/sys/kernel/sched_ext/root/ops=qmap
pid 42566's current scheduling policy: SCHED_EXT
pid 42566's current scheduling priority: 0
ps: PID 42566, COMMAND yes, policy #7
post-test state=disabled
post-test nr_rejected=0
```

Exactly one deliberately created test process (`yes`) was changed to
`SCHED_EXT`; it was terminated before scheduler cleanup completed.
`scx_qmap.c`'s unchanged `-p` option sets
`skel->struct_ops.qmap_ops->flags |= SCX_OPS_SWITCH_PARTIAL`.

This verifies that the kernel accepted partial-mode registration and that one
process could be explicitly opted into policy 7. It does not independently
census every ordinary desktop task, so it is not evidence that no other task
was transiently in sched_ext.

## Phase 1B conclusion

The unchanged official `scx_simple` build and runtime attach/detach test passed.
The unchanged official `scx_qmap -p` partial-mode test passed at the basic
selected-task criterion. No source files were modified and no PulseSched code
was implemented.

## Remaining limitation

The checkout's bundled `vmlinux-v6.18-rc4-g6146a0f1dfae.h` does not exactly
match the running `7.2.9-2-cachyos` kernel. The compiler and runtime produced no
failure attributable to that mismatch, but it remains a reproducibility and
compatibility limitation.

