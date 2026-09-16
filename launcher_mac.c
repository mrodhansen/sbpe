#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
	char libbuf[4096];
	char gamebuf[4096];
	char dirbuf[4096];
	char *dir;

	if (argc < 3) {
		fprintf(stderr, "usage: %s REMOTE.BIN GAME [args...]\n", argv[0]);
		return 1;
	}

	if (realpath(argv[1], libbuf) == NULL) {
		perror("realpath remote.bin");
		return 1;
	}
	if (realpath(argv[2], gamebuf) == NULL) {
		perror("realpath game");
		return 1;
	}

	if (setenv("DYLD_INSERT_LIBRARIES", libbuf, 1) != 0) {
		perror("setenv DYLD_INSERT_LIBRARIES");
		return 1;
	}
	if (setenv("DYLD_FORCE_FLAT_NAMESPACE", "1", 1) != 0) {
		perror("setenv DYLD_FORCE_FLAT_NAMESPACE");
		return 1;
	}

	snprintf(dirbuf, sizeof(dirbuf), "%s", gamebuf);
	dir = dirname(dirbuf);
	if (chdir(dir) != 0) {
		perror("chdir");
		return 1;
	}

	argv[2] = gamebuf;
	execv(gamebuf, &argv[2]);
	perror("execv");
	return 1;
}
