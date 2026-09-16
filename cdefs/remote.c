#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif

// paths are relative to the build dir

#include "../cdefs/internals.h"
#include "../cdefs/generated.h" // needed to define enum values
#include "../cdefs/XDL.h"

#include "../libs/subhook/subhook.h"
#include "../libs/plthook/plthook.h"

#ifdef MS_WIN32
	#include "../libs/SDL/include/SDL.h"
#else
	#include <SDL2/SDL.h>
#endif

#ifndef MS_WIN32

#include <dlfcn.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

uint32_t kickstart(void);

static pthread_once_t sbpe_once = PTHREAD_ONCE_INIT;

static void sbpe_kickstart_once(void) {
	kickstart();
}

static void sbpe_boot(void) {
	pthread_once(&sbpe_once, sbpe_kickstart_once);
}

#ifndef __APPLE__
static int sbpe_SDL_Init(uint32_t flags) {
	int (*orig)(uint32_t);
	sbpe_boot();
	orig = (int(*)(uint32_t))dlsym(RTLD_NEXT, "SDL_Init");
	if (orig == NULL) {
		return -1;
	}
	return orig(flags);
}
#endif

#ifdef __APPLE__
#include <mach-o/dyld.h>

static int sbpe_slide_found;
static long sbpe_slide_value;

static void *sbpe_deferred_boot(void *arg) {
	(void)arg;
	sbpe_boot();
	return NULL;
}

__attribute__((constructor))
static void sbpe_ctor(void) {
	pthread_t t;
	unsetenv("DYLD_INSERT_LIBRARIES");
	unsetenv("DYLD_FORCE_FLAT_NAMESPACE");
	/* Do not DYLD_INTERPOSE SDL_Init: the replacee is this dylib's stub, so
	   the wrapper sees orig==self, returns -1, and XDL_Init abort()s. */
	if (pthread_create(&t, NULL, sbpe_deferred_boot, NULL) == 0) {
		pthread_detach(t);
	}
}

static int sbpe_is_game_image(const char *name) {
	const char *base;
	if (name == NULL || strstr(name, ".dylib") != NULL) {
		return 0;
	}
	base = strrchr(name, '/');
	base = (base == NULL) ? name : base + 1;
	return strcmp(base, "mvmmoclient") == 0 || strcmp(base, "StarBreak") == 0;
}

long sbpe_image_slide(void) {
	uint32_t i, n;
	sbpe_slide_found = 0;
	sbpe_slide_value = 0;
	n = _dyld_image_count();
	for (i = 0; i < n; i++) {
		const char *name = _dyld_get_image_name(i);
		if (sbpe_is_game_image(name)) {
			sbpe_slide_found = 1;
			sbpe_slide_value = (long)_dyld_get_image_vmaddr_slide(i);
			break;
		}
	}
	return sbpe_slide_value;
}

int sbpe_image_found(void) {
	return sbpe_slide_found;
}
#else
/* Linux: LD_PRELOAD interposes by exporting SDL_Init. */
#include <link.h>

static int sbpe_slide_found;
static long sbpe_slide_value;

__attribute__((constructor))
static void sbpe_ctor(void) {
	unsetenv("LD_PRELOAD");
}

CFFI_DLLEXPORT int SDL_Init(uint32_t flags) {
	return sbpe_SDL_Init(flags);
}

static int sbpe_phdr_cb(struct dl_phdr_info *info, size_t size, void *data) {
	const char *name;
	(void)size;
	(void)data;
	name = info->dlpi_name ? info->dlpi_name : "";
	if (strstr(name, "remote.bin") != NULL) {
		return 0;
	}
	if (name[0] == '\0' || strstr(name, "mvmmoclient") != NULL ||
			strstr(name, "StarBreak") != NULL) {
		sbpe_slide_found = 1;
		sbpe_slide_value = (long)info->dlpi_addr;
		return 1;
	}
	return 0;
}

long sbpe_image_slide(void) {
	sbpe_slide_found = 0;
	sbpe_slide_value = 0;
	dl_iterate_phdr(sbpe_phdr_cb, NULL);
	return sbpe_slide_value;
}

int sbpe_image_found(void) {
	return sbpe_slide_found;
}
#endif

#else

long sbpe_image_slide(void) {
	return 0;
}

int sbpe_image_found(void) {
	return 1;
}

#endif
