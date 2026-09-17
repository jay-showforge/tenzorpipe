/* Test-only libc writable-open audit. No engine linkage; no ptrace required.
 * Direct syscalls/static executables can bypass this instrumentation. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>
static void record(const char *path, int flags) {
    if (!(flags & (O_WRONLY | O_RDWR | O_CREAT))) return;
    const char *log = getenv("TENZOR_OPEN_AUDIT");
    if (!log) return;
    int fd = syscall(SYS_openat, AT_FDCWD, log, O_WRONLY|O_APPEND|O_CREAT, 0600);
    if (fd < 0) return;
    char buf[8192];
    int n = snprintf(buf, sizeof(buf), "%ld\t%s\n", (long)getpid(), path);
    if (n > 0 && n < (int)sizeof(buf)) syscall(SYS_write, fd, buf, n);
    syscall(SYS_close, fd);
}
#define OPEN(name) \
int name(const char *p, int f, ...) { \
    mode_t m=0; if ((f&O_CREAT) || (f&O_TMPFILE)==O_TMPFILE) {va_list a;va_start(a,f);m=va_arg(a,int);va_end(a);} \
    int (*real)(const char*,int,...)=dlsym(RTLD_NEXT,#name);record(p,f);return real(p,f,m); }
#define OPENAT(name) \
int name(int d,const char *p,int f,...) { \
    mode_t m=0; if ((f&O_CREAT) || (f&O_TMPFILE)==O_TMPFILE) {va_list a;va_start(a,f);m=va_arg(a,int);va_end(a);} \
    int (*real)(int,const char*,int,...)=dlsym(RTLD_NEXT,#name);record(p,f);return real(d,p,f,m); }
OPEN(open)
OPEN(open64)
OPENAT(openat)
OPENAT(openat64)
int creat(const char *p,mode_t m) {int (*real)(const char*,mode_t)=dlsym(RTLD_NEXT,"creat");record(p,O_CREAT|O_WRONLY);return real(p,m);}
int creat64(const char *p,mode_t m) {int (*real)(const char*,mode_t)=dlsym(RTLD_NEXT,"creat64");record(p,O_CREAT|O_WRONLY);return real(p,m);}
#define FOPEN(name) \
FILE *name(const char *p,const char *m) { \
    FILE *(*real)(const char*,const char*)=dlsym(RTLD_NEXT,#name); \
    if (m[0]=='w'||m[0]=='a'||__builtin_strchr(m,'+')) record(p,O_WRONLY);return real(p,m); }
FOPEN(fopen)
FOPEN(fopen64)
