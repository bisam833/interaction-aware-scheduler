/* SPDX-License-Identifier: MIT */
#ifndef PULSE_EXTERNAL_SCHEDULER_H
#define PULSE_EXTERNAL_SCHEDULER_H
#include <limits.h>

/* Explicit inherited descriptors from the owner runner. Never discovers or
 * attaches a scheduler. The ordinary benchmark build does not use this API. */
static int external_fd(const char *name)
{
	const char *s = getenv(name);
	char *end;
	if (!s || !*s) return -1;
	errno = 0;
	long fd = strtol(s, &end, 10);
	if (errno || *end || fd < 3 || fd > INT_MAX || fcntl(fd, F_GETFD) < 0)
		return -1;
	return (int)fd;
}

static int external_map_valid(int fd, const char *name, uint32_t type,
			      uint32_t size, uint32_t entries)
{
	struct bpf_map_info info = {0};
	uint32_t len = sizeof(info);
	return !bpf_obj_get_info_by_fd(fd, &info, &len) && info.type == type &&
		info.key_size == sizeof(uint32_t) && info.value_size == size &&
		info.max_entries == entries && !strncmp(info.name, name, sizeof(info.name));
}

static int external_pidfd_valid(int fd)
{
	char path[64], target[64];
	snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
	ssize_t n = readlink(path, target, sizeof(target)-1);
	if (n < 0) return 0;
	target[n] = 0;
	return !strcmp(target, "anon_inode:[pidfd]");
}

static int external_alive(int fd)
{
	struct pollfd p = {.fd=fd, .events=POLLIN};
	int rc;
	do { rc = poll(&p, 1, 0); } while (rc < 0 && errno == EINTR);
	return rc == 0;
}
#endif
