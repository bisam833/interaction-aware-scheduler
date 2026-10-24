BUILD := build
ROOT := ../..
PULSE := $(ROOT)/src/pulsesched
SCX := $(ROOT)/external/scx-c-examples
PROTO := /usr/share/wayland-protocols
WAYLAND_CFLAGS := $(shell pkg-config --cflags wayland-client)
WAYLAND_LIBS := $(shell pkg-config --libs wayland-client)
LIBBPF_CFLAGS := $(shell pkg-config --cflags libbpf)
LIBBPF_LIBS := $(shell pkg-config --libs libbpf)
CFLAGS := -std=gnu11 -Wall -Wextra -Wpedantic -O2 -g $(WAYLAND_CFLAGS) \
	-I$(PULSE) -I$(PULSE)/build -I$(SCX)/scheds/include -I$(SCX)/scheds/vmlinux \
	-I$(SCX)/scheds/vmlinux/arch/x86 $(LIBBPF_CFLAGS)

all: $(BUILD)/pulsesched-wayland-bench $(BUILD)/pulse-load

$(BUILD)/pulse-load: load.c | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra $< -o $@

$(PULSE)/build/pulsesched.bpf.skel.h: FORCE
	$(MAKE) -C $(PULSE) build/pulsesched.bpf.skel.h

$(BUILD):
	mkdir -p $@

$(BUILD)/presentation-time-client-protocol.h: $(PROTO)/stable/presentation-time/presentation-time.xml | $(BUILD)
	wayland-scanner client-header $< $@

$(BUILD)/presentation-time-protocol.c: $(PROTO)/stable/presentation-time/presentation-time.xml | $(BUILD)
	wayland-scanner private-code $< $@

$(BUILD)/xdg-shell-client-protocol.h: $(PROTO)/stable/xdg-shell/xdg-shell.xml | $(BUILD)
	wayland-scanner client-header $< $@

$(BUILD)/xdg-shell-protocol.c: $(PROTO)/stable/xdg-shell/xdg-shell.xml | $(BUILD)
	wayland-scanner private-code $< $@

$(BUILD)/pulsesched-wayland-bench: benchmark.c $(PULSE)/pulse_policy.h \
		$(PULSE)/build/pulsesched.bpf.skel.h \
		$(BUILD)/presentation-time-client-protocol.h \
		$(BUILD)/presentation-time-protocol.c \
		$(BUILD)/xdg-shell-client-protocol.h \
		$(BUILD)/xdg-shell-protocol.c
	$(CC) $(CFLAGS) -I$(BUILD) benchmark.c \
		$(BUILD)/presentation-time-protocol.c $(BUILD)/xdg-shell-protocol.c \
		-o $@ $(WAYLAND_LIBS) $(LIBBPF_LIBS) -lelf -lz -lzstd -lpthread

clean:
	rm -rf $(BUILD)

$(BUILD)/feedback-lifecycle-test: $(ROOT)/tests/feedback_lifecycle_test.c $(BUILD)/pulsesched-wayland-bench
	$(CC) $(CFLAGS) -I$(BUILD) -fsanitize=address,undefined $< \
		$(BUILD)/presentation-time-protocol.c $(BUILD)/xdg-shell-protocol.c \
		-o $@ $(WAYLAND_LIBS) $(LIBBPF_LIBS) -lelf -lz -lzstd -lpthread

check: $(BUILD)/feedback-lifecycle-test
	ASAN_OPTIONS=detect_leaks=0 $(BUILD)/feedback-lifecycle-test
	$(BUILD)/pulsesched-wayland-bench --selftest-workers

# Separate output preserves the exact host-matrix baseline executable.
$(BUILD)/prophet-wayland-bench: benchmark.c external_scheduler.h $(PULSE)/pulse_policy.h \
		$(PULSE)/build/pulsesched.bpf.skel.h \
		$(BUILD)/presentation-time-client-protocol.h $(BUILD)/presentation-time-protocol.c \
		$(BUILD)/xdg-shell-client-protocol.h $(BUILD)/xdg-shell-protocol.c
	$(CC) $(CFLAGS) -DPULSE_EXTERNAL_BACKEND -I$(BUILD) benchmark.c \
		$(BUILD)/presentation-time-protocol.c $(BUILD)/xdg-shell-protocol.c \
		-o $@ $(WAYLAND_LIBS) $(LIBBPF_LIBS) -lelf -lz -lzstd -lpthread

$(BUILD)/prophet-backend-test: $(ROOT)/tests/prophet_backend_test.c benchmark.c external_scheduler.h \
		$(BUILD)/prophet-wayland-bench
	$(CC) $(CFLAGS) -I$(BUILD) -fsanitize=address,undefined $< \
		$(BUILD)/presentation-time-protocol.c $(BUILD)/xdg-shell-protocol.c \
		-o $@ $(WAYLAND_LIBS) $(LIBBPF_LIBS) -lelf -lz -lzstd -lpthread

$(BUILD)/prophet-feedback-test: $(ROOT)/tests/feedback_lifecycle_test.c benchmark.c \
		$(BUILD)/prophet-wayland-bench
	$(CC) $(CFLAGS) -I$(BUILD) -fsanitize=address,undefined $< \
		$(BUILD)/presentation-time-protocol.c $(BUILD)/xdg-shell-protocol.c \
		-o $@ $(WAYLAND_LIBS) $(LIBBPF_LIBS) -lelf -lz -lzstd -lpthread

prophet-check: $(BUILD)/prophet-backend-test $(BUILD)/prophet-feedback-test
	ASAN_OPTIONS=detect_leaks=0 $(BUILD)/prophet-backend-test
	ASAN_OPTIONS=detect_leaks=0 $(BUILD)/prophet-feedback-test
	$(BUILD)/prophet-wayland-bench --selftest-workers

FORCE:

.PHONY: all clean check prophet-check FORCE
