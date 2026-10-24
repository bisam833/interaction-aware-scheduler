# Environment audit

Audit date: 2026-10-13 (Asia/Kolkata)

## Repository

- Working directory: `/home/bisam/osproject`
- Existing project files before this audit: none.
- `.git` is an empty read-only `tmpfs` mount (`ro,nosuid,nodev`) and is busy;
  `rmdir .git` fails with `Device or resource busy`. It contains no metadata.
- A separate explicit Git directory `.git-repo` was initialized only because
  the host mount cannot be replaced. The Phase 0 commit is `9b8abc2`; commands
  must use `GIT_DIR=.git-repo GIT_WORK_TREE=.` until the mount is fixed.

## Host

- Distribution: CachyOS rolling (`ID_LIKE=arch`)
- Kernel: `7.2.9-2-cachyos`, x86_64, `PREEMPT_DYNAMIC`
- CPU: AMD Ryzen 7 7840U with Radeon 780M; 1 socket, 8 cores, 16 logical CPUs;
  CPUs 0–15 online; one NUMA node; boost enabled.
- CPU topology and frequency are platform-specific and must not be treated as
  controlled experiment results.

## Kernel and BPF

The installed kernel config reports:

- `CONFIG_SCHED_CLASS_EXT=y`
- `CONFIG_BPF=y`
- `CONFIG_BPF_SYSCALL=y`
- `CONFIG_BPF_JIT=y`
- `CONFIG_DEBUG_INFO_BTF=y`
- `CONFIG_CGROUPS=y`

`/sys/kernel/sched_ext` exists. At audit time:

```text
state=disabled
enable_seq=0
nr_rejected=0
switch_all=0
hotplug_seq=15
```

The current process is UID 1000 with no effective capabilities and `no-new-privs`.
`bpftool feature probe kernel` cannot perform a full probe because
`CAP_SYS_ADMIN`, `CAP_BPF`, `CAP_NET_ADMIN`, and `CAP_PERFMON` are unavailable.
This is an access limitation, not evidence that BPF or sched_ext cannot work.

`sudo -n -v` also fails: `no-new-privs` prevents privilege elevation and
`/etc/sudo.conf` is not root-owned in this environment. No scheduler load was
attempted.

## Toolchain and installed sched_ext software

- Clang/LLVM: 23.1.1
- GCC: 16.2.1
- bpftool: 7.8.0, using libbpf 1.8 according to its binary
- installed libbpf package/pkg-config: 1.7.0
- installed kernel package: `scx-scheds` 1.1.3-2
- installed loader package: `scx-tools` 1.1.3-1.1
- installed sched_ext executables include `scx_rusty`, `scx_rustland`,
  `scx_cake`, `scx_bpfland`, and others. The installed package inspection found
  binaries, not the corresponding source tree.

The implementation base is not `scx_rusty`. The installed kernel build tree
contains headers and `vmlinux.h` but not `tools/sched_ext` source. The selected
source baseline remains **not selected** until the source corresponding to this
kernel/package is obtained. The preferred target is kernel-matched
`scx_simple` or an officially compatible C example; current upstream source
must not be assumed compatible. No candidate has been loaded.

The kernel-matched headers expose `SCX_OPS_SWITCH_PARTIAL` and
`SCHED_EXT=7`. This supports the intended development model in principle:
when the flag is set, only tasks explicitly assigned policy `SCHED_EXT` should
be handled by sched_ext. Actual opt-in behavior remains untested.

## Display and input stack

- Session type: Wayland
- Desktop/compositor: GNOME Shell 51.0
- `WAYLAND_DISPLAY=wayland-0`; `DISPLAY=:0` is also set for Xwayland use.
- Wayland client development package: 1.26.0
- Wayland protocols package: 1.49
- `presentation-time.xml` is installed under the stable Wayland protocols.
- `wayland-scanner` is installed.
- libinput package: 1.32.0
- No `/dev/input` device directory is visible in this restricted environment;
  direct evdev input capture is therefore not currently available.

The manual `wayland-info` check verified that the running compositor advertises
`wp_presentation` version 2 and reports presentation clock ID 1
(`CLOCK_MONOTONIC`). This establishes protocol capability only; it does not yet
establish causal input-to-frame attribution.

## Benchmark dependencies

- `stress-ng` was not found in the audited command path.
- `perf` was not found in the audited command path.
- Reproducible load and measurement dependencies must be provisioned and
  recorded before Phase 3/4 experiments.

## Reproducibility boundary

Do not upgrade the kernel, compiler, sched_ext packages, or desktop stack as a
first response to a failure. First distinguish permission, package/source,
kernel ABI, and protocol-attribution failures. Any necessary environment
change must be recorded in `docs/decisions.md` and approved by the human.

