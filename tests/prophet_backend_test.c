/* Exercise the actual external backend and epoch lifecycle with map syscalls
 * mocked. pidfd ownership/death and descriptor cleanup use real kernel fds. */
#define PULSE_EXTERNAL_BACKEND
#define main benchmark_main
#include "../benchmarks/wayland_interaction/benchmark.c"
#undef main
#include <assert.h>
#include <sys/wait.h>

static int task_fd, metric_fd, corrupt_schema, updates;
static struct pulse_task_state stored;

int bpf_obj_get_info_by_fd(int fd, void *info, __u32 *len)
{
	struct bpf_map_info *m = info;
	if (fd != task_fd && fd != metric_fd) { errno = EBADF; return -1; }
	assert(*len == sizeof(*m));
	m->type = fd == task_fd ? BPF_MAP_TYPE_HASH : BPF_MAP_TYPE_PERCPU_ARRAY;
	m->key_size = 4;
	m->value_size = fd == task_fd ? sizeof(stored) : 8;
	m->max_entries = fd == task_fd ? 1024 : 12;
	strcpy(m->name, fd == task_fd ? "task_state" : "metrics");
	if (corrupt_schema) m->value_size++;
	return 0;
}

int bpf_map_update_elem(int fd, const void *key, const void *value, __u64 flags)
{
	(void)key; (void)flags;
	assert(fd == task_fd);
	stored = *(const struct pulse_task_state *)value;
	updates++;
	return 0;
}

int bpf_map_lookup_elem(int fd, const void *key, void *value)
{
	(void)key;
	assert(fd == task_fd);
	*(struct pulse_task_state *)value = stored;
	return 0;
}

int bpf_map_get_next_key(int fd, const void *key, void *next)
{
	(void)key; (void)next;
	assert(fd == task_fd);
	errno = ENOENT;
	return -1;
}

int sched_setscheduler(pid_t pid, int policy, const struct sched_param *param)
{
	(void)param; assert(pid == 0 && policy == SCHED_EXT); return 0;
}
int sched_getscheduler(pid_t pid) { assert(pid == 0); return SCHED_EXT; }

static void set_fd(const char *name, int fd)
{
	char value[32]; snprintf(value, sizeof(value), "%d", fd); setenv(name, value, 1);
}

int main(void)
{
	assert(external_fd("PROPHET_TASK_FD") == -1);
	setenv("PROPHET_TASK_FD", "0", 1); assert(external_fd("PROPHET_TASK_FD") == -1);
	setenv("PROPHET_TASK_FD", "999999999999999999999", 1);
	assert(external_fd("PROPHET_TASK_FD") == -1);
	task_fd = open("/dev/null", O_RDONLY); metric_fd = open("/dev/null", O_RDONLY);
	assert(task_fd >= 3 && metric_fd >= 3);
	assert(!external_pidfd_valid(task_fd));
	pid_t child = fork(); assert(child >= 0);
	if (!child) { for (;;) pause(); }
	int pidfd = syscall(SYS_pidfd_open, child, 0);
	assert(pidfd >= 0 && external_pidfd_valid(pidfd) && external_alive(pidfd));
	set_fd("PROPHET_TASK_FD", task_fd); set_fd("PROPHET_METRICS_FD", metric_fd);
	set_fd("PROPHET_PID_FD", pidfd);
	struct app app = {.pulse_ttl_ms=250, .max_propagation_depth=2, .closed_loop=1};
	assert(pulse_init(&app) == 0);
	assert(app.pulse.enabled && app.pulse.external && !app.pulse.skel && !app.pulse.link);
	assert(pulse_task_fd(&app) == task_fd && pulse_metrics_fd(&app) == metric_fd);
	assert(!pulse_exited(&app));
	struct pulse_task_state state = {.epoch_id=1, .active=1, .direct=1,
		.start_ns=clock_ns(CLOCK_MONOTONIC), .deadline_ns=clock_ns(CLOCK_MONOTONIC)+250000000};
	assert(pulse_update_state(&app, &state) == 0);
	assert(pulse_terminate(&app, 1, 1) == 1);
	assert(!stored.active && stored.termination_reason == 1 && updates >= 3);
	corrupt_schema = 1;
	assert(!external_map_valid(task_fd, "task_state", BPF_MAP_TYPE_HASH, sizeof(stored), 1024));
	corrupt_schema = 0;
	kill(child, SIGTERM); assert(waitpid(child, NULL, 0) == child);
	assert(pulse_exited(&app));
	pulse_cleanup(&app); pulse_cleanup(&app);
	assert(fcntl(task_fd, F_GETFD) == -1 && fcntl(metric_fd, F_GETFD) == -1);
	assert(fcntl(pidfd, F_GETFD) == -1);
	puts("PASS: external fd/ABI validation, epoch closure, owner death, cleanup; map syscalls mocked");
}
