# Phase 2 project-owned scheduler build.
# The include scaffold and vmlinux.h are reused read-only from the validated
# official C-example checkout; the scheduler sources below are PulseSched code.

ROOT := ../..
SCX := $(ROOT)/external/scx-c-examples
BUILD := build
SCHED_INCLUDE := $(SCX)/scheds/include
VMLINUX_INCLUDE := $(SCX)/scheds/vmlinux
VMLINUX_ARCH_INCLUDE := $(SCX)/scheds/vmlinux/arch/x86
LIBBPF_CFLAGS := $(shell pkg-config --cflags libbpf)
LIBBPF_LIBS := $(shell pkg-config --libs libbpf)

BPF_CFLAGS := -g -O2 -Wall -Wno-compare-distinct-pointer-types \
	-D__TARGET_ARCH_x86 -mcpu=v3 -mlittle-endian -target bpf \
	-I$(SCHED_INCLUDE) -I$(SCHED_INCLUDE)/bpf-compat \
	-I$(SCHED_INCLUDE)/lib -I$(VMLINUX_INCLUDE) \
	-I$(VMLINUX_ARCH_INCLUDE) $(LIBBPF_CFLAGS)
CFLAGS := -std=gnu11 -Wall -Wextra -O2 -g \
	-I$(SCHED_INCLUDE) -I$(VMLINUX_INCLUDE) -I$(VMLINUX_ARCH_INCLUDE) \
	-I$(BUILD) $(LIBBPF_CFLAGS)
LIBS := $(LIBBPF_LIBS) -lelf -lz -lzstd -lpthread

all: $(BUILD)/pulsesched $(BUILD)/pulsesched_ctl

$(BUILD):
	mkdir -p $@

$(BUILD)/pulsesched.bpf.o: pulsesched.bpf.c pulse_policy.h | $(BUILD)
	clang $(BPF_CFLAGS) -c $< -o $@

$(BUILD)/pulsesched.bpf.skel.h: $(BUILD)/pulsesched.bpf.o
	bpftool gen skeleton $< name pulsesched > $@

$(BUILD)/pulsesched: pulsesched.c $(BUILD)/pulsesched.bpf.skel.h
	cc $(CFLAGS) $< -o $@ $(LIBS)

$(BUILD)/pulsesched_ctl: pulsesched_ctl.c $(BUILD)/pulsesched.bpf.skel.h
	cc $(CFLAGS) $< -o $@ $(LIBS)

clean:
	rm -rf $(BUILD)

.PHONY: all clean
