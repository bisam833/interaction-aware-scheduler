# Known limitations

- No scheduler implementation or benchmark exists yet.
- Sched_ext support is visible in the kernel, but this session cannot perform a
  privileged load/probe.
- The installed sched_ext package does not provide a local source tree for
  inspection in this workspace.
- Phase 1A binary smoke testing works, but Phase 1B cannot establish a
  kernel-matched unchanged source build without a source retrieval path.
- GNOME/Wayland presentation-time support and callback attribution are
  unverified.
- Direct input-device access is unavailable in the current restricted view.
- No measurements, plots, or performance claims exist.
- CPU-frequency control is out of scope for the core experiment.

