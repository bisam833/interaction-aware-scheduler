/* SPDX-License-Identifier: GPL-2.0 */
/* Project-owned Phase 2 PulseSched loader and state reporter. */
#include <stdio.h>
#include <signal.h>
#include <string.h>
#include <unistd.h>
#include <bpf/bpf.h>
#include <scx/common.h>
#include "pulsesched.bpf.skel.h"

static volatile sig_atomic_t stop;
static void on_signal(int sig) { (void)sig; stop = 1; }

int main(int argc, char **argv)
{
	struct pulsesched *skel;
	struct bpf_link *link;
	__u64 ecode;
	bool propagation = false, trace_delay = false, partial = false;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--propagation"))
			propagation = true;
		else if (!strcmp(argv[i], "--trace-delay"))
			trace_delay = true;
		else if (!strcmp(argv[i], "--partial"))
			partial = true;
		else {
			fprintf(stderr, "usage: %s [--propagation] [--trace-delay] [--partial]\n", argv[0]);
			return 2;
		}
	}
	libbpf_set_strict_mode(LIBBPF_STRICT_ALL);
	signal(SIGINT, on_signal);
	signal(SIGTERM, on_signal);
	skel = SCX_OPS_OPEN(pulsesched_ops, pulsesched);
	skel->rodata->pulse_propagation_enabled = propagation;
	skel->rodata->pulse_trace_delay = trace_delay;
	if (partial)
		skel->struct_ops.pulsesched_ops->flags |= SCX_OPS_SWITCH_PARTIAL;
	SCX_OPS_LOAD(skel, pulsesched_ops, pulsesched, uei);
	link = SCX_OPS_ATTACH(skel, pulsesched_ops, pulsesched);
	printf("pulsesched attached partial=%d propagation=%d trace_delay=%d policy=bounded-vtime\n",
	       partial, propagation, trace_delay);
	fflush(stdout);
	while (!stop && !UEI_EXITED(skel, uei))
		sleep(1);
	bpf_link__destroy(link);
	ecode = UEI_REPORT(skel, uei);
	pulsesched__destroy(skel);
	return UEI_ECODE_USER(ecode) ? 1 : 0;
}
