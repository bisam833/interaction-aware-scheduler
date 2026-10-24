ROOT := ../..
SCX := $(ROOT)/external/scx-c-examples
BUILD := build
INC := -I$(SCX)/scheds/include -I$(SCX)/scheds/vmlinux -I$(SCX)/scheds/vmlinux/arch/x86
LIBS := $(shell pkg-config --libs libbpf) -lelf -lz -lzstd -lpthread
BPF_CFLAGS := -g -O2 -Wall -Wno-compare-distinct-pointer-types -D__TARGET_ARCH_x86 -mcpu=v3 -mlittle-endian -target bpf $(INC) -I$(SCX)/scheds/include/bpf-compat -I$(SCX)/scheds/include/lib
CFLAGS := -std=gnu11 -Wall -Wextra -O2 -g $(INC) -I$(BUILD)

all: $(BUILD)/scx_prophet
$(BUILD):
	mkdir -p $@
$(BUILD)/prophet.bpf.o: prophet.bpf.c prophet.h ../pulsesched/pulse_policy.h | $(BUILD)
	clang $(BPF_CFLAGS) -c $< -o $@
$(BUILD)/prophet.bpf.skel.h: $(BUILD)/prophet.bpf.o
	bpftool gen skeleton $< name prophet > $@.tmp
	mv $@.tmp $@
$(BUILD)/scx_prophet: prophet.c control.h prophet.h $(BUILD)/prophet.bpf.skel.h
	$(CC) $(CFLAGS) $< -o $@ $(LIBS)
$(BUILD)/prophet-policy-test: $(ROOT)/tests/prophet_policy_test.c control.h prophet.h ../pulsesched/pulse_policy.h | $(BUILD)
	$(CC) -std=gnu11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined $< -o $@
check: $(BUILD)/prophet-policy-test
	ASAN_OPTIONS=detect_leaks=0 $(BUILD)/prophet-policy-test
clean:
	rm -rf $(BUILD)
.PHONY: all check clean
