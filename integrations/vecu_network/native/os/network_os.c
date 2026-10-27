/* AXIOM external-tick application for the actual Trampoline POSIX kernel.
 * This application is GPL-2.0-or-later; upstream kernel file licenses apply
 * independently. It contains no autoas code and no software OS scheduler. */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "tpl_os.h"
#include "tpl_posix_internal.h"

#define PROTOCOL "vecu-network-os/1"
#define COMMIT "ff287023252bf73273067f00d2dbc8bbc9a20401"
#define MAX_TICK INT64_C(2147483647)
static int64_t tick_ms = 0;
static unsigned epoch = 0;
static bool initialized = false, reset_bootstrap = false;
static bool suppress_control = false, bsw_due = false, control_due = false;
static unsigned bsw_count = 0, control_count = 0, suppressed_count = 0;
static const char *events[16];
static size_t event_count;

DeclareTask(Driver);
DeclareTask(BswTask);
DeclareTask(ControlTask);
DeclareTask(ControlDispatch);
DeclareEvent(ControlEvent);
DeclareAlarm(BswAlarm);
DeclareAlarm(ControlAlarm);

static void event(const char *name) {
  if (event_count >= sizeof(events) / sizeof(events[0])) {
    fputs("OS event trace overflow\n", stderr); exit(71);
  }
  events[event_count++] = name;
}

static void reply(const char *op, bool ok, const char *error) {
  printf("{\"protocol\":\"%s\",\"op\":\"%s\",\"ok\":%s,"
         "\"engine\":\"trampoline-posix\",\"upstream_commit\":\"%s\","
         "\"tick_ms\":%" PRId64 ",\"epoch\":%u,\"pid\":%ld,"
         "\"bsw_due\":%s,\"control_due\":%s,\"bsw_count\":%u,"
         "\"control_count\":%u,\"suppressed_count\":%u,\"events\":[",
         PROTOCOL, op, ok ? "true" : "false", COMMIT, tick_ms, epoch,
         (long)getpid(), bsw_due ? "true" : "false", control_due ? "true" : "false",
         bsw_count, control_count, suppressed_count);
  for (size_t i = 0; i < event_count; ++i)
    printf("%s{\"name\":\"%s\",\"tick_ms\":%" PRId64 ",\"epoch\":%u}",
           i ? "," : "", events[i], tick_ms, epoch);
  printf("]%s%s%s}\n", error ? ",\"error\":\"" : "", error ? error : "", error ? "\"" : "");
  fflush(stdout);
}

void ErrorHook(StatusType status) {
  fprintf(stderr, "Trampoline ErrorHook: %u at tick %" PRId64 "\n", (unsigned)status, tick_ms);
  reply("error", false, "kernel_service_error");
  exit(72);
}

/* Restricted, strict JSON object parser. This protocol has only flat scalars;
 * unknown fields, duplicate fields, floats, escapes and nested values fail. */
typedef struct { char op[16]; int64_t tick; bool has_tick, suppress; } Request;
static void space(const char **p) { while (isspace((unsigned char)**p)) ++*p; }
static bool string(const char **p, char *dst, size_t size) {
  space(p); if (*(*p)++ != '"') return false;
  size_t n = 0;
  while (**p && **p != '"') {
    if ((unsigned char)**p < 32 || **p == '\\' || n + 1 >= size) return false;
    dst[n++] = *(*p)++;
  }
  if (**p != '"') return false;
  ++*p; dst[n] = 0; return true;
}
static bool parse(const char *p, Request *r) {
  unsigned seen = 0; memset(r, 0, sizeof(*r));
  space(&p); if (*p++ != '{') return false;
  space(&p);
  while (*p != '}') {
    char key[40], value[48]; unsigned bit;
    if (!string(&p, key, sizeof(key))) return false;
    space(&p); if (*p++ != ':') return false;
    space(&p);
    if (!strcmp(key, "op")) {
      bit = 1; if (!string(&p, r->op, sizeof(r->op))) return false;
    } else if (!strcmp(key, "tick_ms")) {
      bit = 2; if (!isdigit((unsigned char)*p)) return false;
      if (*p == '0' && isdigit((unsigned char)p[1])) return false;
      errno = 0; char *end; r->tick = strtoll(p, &end, 10);
      if (errno || r->tick > MAX_TICK) return false;
      p = end; r->has_tick = true;
    } else if (!strcmp(key, "suppress_control_event")) {
      bit = 4;
      if (!strncmp(p, "true", 4)) { r->suppress = true; p += 4; }
      else if (!strncmp(p, "false", 5)) p += 5;
      else return false;
    } else if (!strcmp(key, "protocol")) {
      bit = 8; if (!string(&p, value, sizeof(value)) || strcmp(value, PROTOCOL)) return false;
    } else return false;
    if (seen & bit) return false;
    seen |= bit; space(&p);
    if (*p == '}') break;
    if (*p++ != ',') return false;
    space(&p); if (*p == '}') return false;
  }
  ++p; space(&p); return *p == 0 && (seen & 1);
}

/* Unbuffered reads preserve the next request across reset's execve. */
static int line(char *dst, size_t size) {
  size_t used = 0;
  for (;;) {
    char c; ssize_t n = read(STDIN_FILENO, &c, 1);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) return used ? -1 : 0;
    if (c == '\n') { dst[used] = 0; return 1; }
    if (c == 0 || used + 1 >= size) return -1;
    dst[used++] = c;
  }
}

TASK(BswTask) {
  event("BswAlarm.activate"); event("BswTask.run");
  bsw_due = true; ++bsw_count;
  TerminateTask();
}

TASK(ControlDispatch) {
  event("ControlAlarm.activate");
  if (suppress_control) {
    event("ControlEvent.suppressed"); ++suppressed_count;
  } else {
    event("ControlEvent.set"); SetEvent(ControlTask, ControlEvent);
  }
  TerminateTask();
}

TASK(ControlTask) {
  for (;;) {
    WaitEvent(ControlEvent);
    ClearEvent(ControlEvent);
    event("ControlTask.run");
    event("ControlTask.control_request"); control_due = true; ++control_count;
  }
}

static void arm_alarms(void) {
  SetRelAlarm(BswAlarm, 10 - (tick_ms % 10), 10);
  SetRelAlarm(ControlAlarm, 50 - (tick_ms % 50), 50);
}

TASK(Driver) {
  if (reset_bootstrap) {
    initialized = true; arm_alarms(); event("OS.reset"); reply("reset", true, NULL);
  }
  char input[1024]; int read_status;
  while ((read_status = line(input, sizeof(input))) == 1) {
    event_count = 0; bsw_due = control_due = false;
    Request r;
    if (!parse(input, &r)) { reply("error", false, "invalid_request"); exit(64); }
    if (!strcmp(r.op, "init")) {
      if (initialized || r.has_tick || r.suppress) { reply("init", false, "invalid_init"); exit(64); }
      initialized = true; arm_alarms(); event("OS.start"); reply("init", true, NULL);
    } else if (!strcmp(r.op, "tick")) {
      if (!initialized || !r.has_tick || r.tick != tick_ms + 1) {
        reply("tick", false, "tick_must_advance_exactly_one_ms"); exit(64);
      }
      tick_ms = r.tick; suppress_control = r.suppress;
      /* Kernel counter interrupt -> real alarms -> priority scheduler -> tasks.
       * Driver's lower priority means this returns only after ready tasks finish. */
      raise(signal_for_counters);
      reply("tick", true, NULL);
    } else if (!strcmp(r.op, "reset")) {
      if (!initialized || r.has_tick || r.suppress || epoch == UINT32_MAX) {
        reply("reset", false, "invalid_reset"); exit(64);
      }
      char epoch_arg[24], tick_arg[32];
      snprintf(epoch_arg, sizeof(epoch_arg), "%u", epoch + 1);
      snprintf(tick_arg, sizeof(tick_arg), "%" PRId64, tick_ms);
      char *args[] = {"axiom-network-os", "--reset-bootstrap", epoch_arg, tick_arg, NULL};
      execv("/proc/self/exe", args);
      reply("reset", false, "kernel_restart_failed"); exit(73);
    } else if (!strcmp(r.op, "close")) {
      if (r.has_tick || r.suppress) { reply("close", false, "invalid_close"); exit(64); }
      event("OS.shutdown"); reply("close", true, NULL); ShutdownOS(E_OK);
    } else { reply("error", false, "unsupported_operation"); exit(64); }
  }
  if (read_status < 0) { reply("error", false, "invalid_line"); exit(64); }
  ShutdownOS(E_OK);
}

int main(int argc, char **argv) {
  if (argc == 4 && !strcmp(argv[1], "--reset-bootstrap")) {
    char *end;
    unsigned long value = strtoul(argv[2], &end, 10);
    if (*end || value > UINT32_MAX) return 64;
    epoch = (unsigned)value;
    tick_ms = strtoll(argv[3], &end, 10);
    if (*end || tick_ms < 0 || tick_ms > MAX_TICK) return 64;
    reset_bootstrap = true;
  } else if (argc != 1) return 64;
  StartOS(OSDEFAULTAPPMODE);
  return 0;
}
