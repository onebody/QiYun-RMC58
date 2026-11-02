/* The relay's sweep-head byte pipe: every byte the head's UART emits goes out
 * the uplink unchanged, and command lines go back the other way. No parsing, no
 * envelope - the frames carry their own 0xA5 sync and CRC and the platform's
 * stream parser owns resync (spec 分歧 B; the same choice web/serve.mjs's
 * attachNode() makes). GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>

void relay_pipe_start(void);
/* One read chunk of the head's bytes as one WS binary frame. Concatenation,
 * not message boundaries, is what carries the stream - so a frame may straddle
 * two chunks and rftrack/backend/app/services/frame_parser.py has to cope;
 * Task 8 is the test that says it does. */
void relay_pipe_status(char *out, size_t cap);
/* "HEAD <line>" - the rest of the command line, verbatim, to the head. */
bool relay_pipe_send(const char *line);
