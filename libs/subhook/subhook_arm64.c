/*
 * Copyright (c) 2012-2018 Zeex
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "subhook.h"
#include "subhook_private.h"

#pragma pack(push, 1)

struct subhook_jmp_arm64 {
  uint32_t ldr_x16; /* LDR X16, #8 */
  uint32_t br_x16;  /* BR X16 */
  uint64_t target;  /* 64-bit target address */
};

#pragma pack(pop)

extern subhook_disasm_handler_t subhook_disasm_handler;

SUBHOOK_EXPORT int SUBHOOK_API subhook_disasm(void *src, int *reloc_op_offset) {
  if (reloc_op_offset != NULL) {
    *reloc_op_offset = 0;
  }
  /* On ARM64 all instructions are 4 bytes long.
   * Simple implementation: we don't relocate PC-relative instructions yet.
   */
  return 4;
}

static size_t subhook_get_jmp_size(subhook_flags_t flags) {
  (void)flags;
  return sizeof(struct subhook_jmp_arm64);
}

static int subhook_make_jmp(void *src, void *dst, subhook_flags_t flags) {
  struct subhook_jmp_arm64 *jmp = (struct subhook_jmp_arm64 *)src;
  (void)flags;

  /* LDR X16, #8
   * Opcode: 0x58000050
   * 0x58: LDR (literal)
   * 0x000005: offset #8 (8 >> 2 = 2, but encoding is slightly different)
   * Actually #8 is 0x40 in some contexts, but for LDR (literal) 64-bit:
   * imm19 is offset/4. For +8 bytes, imm19 = 2.
   * bits[23:5] = 2.
   * 0x58000040 | (2 << 5) = 0x58000050
   */
  jmp->ldr_x16 = 0x58000050;
  /* BR X16
   * Opcode: 0xD61F0200
   */
  jmp->br_x16 = 0xD61F0200;
  jmp->target = (uint64_t)dst;

  return 0;
}

static int subhook_make_trampoline(void *trampoline,
                                   void *src,
                                   size_t jmp_size,
                                   size_t *trampoline_len,
                                   subhook_flags_t flags) {
  size_t orig_size = 0;
  size_t insn_len;
  uintptr_t trampoline_addr = (uintptr_t)trampoline;
  uintptr_t src_addr = (uintptr_t)src;
  subhook_disasm_handler_t disasm_handler =
    subhook_disasm_handler != NULL ? subhook_disasm_handler : subhook_disasm;

  assert(trampoline_len != NULL);

  while (orig_size < jmp_size) {
    insn_len = disasm_handler((void *)(src_addr + orig_size), NULL);

    if (insn_len == 0) {
      return -EINVAL;
    }

    memcpy((void *)(trampoline_addr + orig_size),
           (void *)(src_addr + orig_size),
           insn_len);

    orig_size += insn_len;
  }

  *trampoline_len = orig_size + jmp_size;

  return subhook_make_jmp((void *)(trampoline_addr + orig_size),
                          (void *)(src_addr + orig_size),
                          flags);
}

SUBHOOK_EXPORT subhook_t SUBHOOK_API subhook_new(void *src,
                                                 void *dst,
                                                 subhook_flags_t flags) {
  subhook_t hook;
  int error;

  hook = calloc(1, sizeof(*hook));
  if (hook == NULL) {
    return NULL;
  }

  hook->src = src;
  hook->dst = dst;
  hook->flags = flags;
  hook->jmp_size = subhook_get_jmp_size(hook->flags);
  hook->trampoline_size = hook->jmp_size * 2 + 4;

  hook->code = malloc(hook->jmp_size);
  if (hook->code == NULL) {
    goto error_exit;
  }

  memcpy(hook->code, hook->src, hook->jmp_size);

  error = subhook_unprotect(hook->src, hook->jmp_size);
  if (error != 0) {
    goto error_exit;
  }

  hook->trampoline = subhook_alloc_code(hook->trampoline_size);
  if (hook->trampoline != NULL) {
    error = subhook_make_trampoline(hook->trampoline,
                                    hook->src,
                                    hook->jmp_size,
                                    &hook->trampoline_len,
                                    hook->flags);
    if (error != 0) {
      subhook_free_code(hook->trampoline, hook->trampoline_size);
      hook->trampoline = NULL;
      hook->trampoline_size = 0;
      hook->trampoline_len = 0;
    }
  }

  return hook;

error_exit:
  subhook_free_code(hook->trampoline, hook->trampoline_size);
  if (hook->code != NULL) {
    free(hook->code);
  }
  free(hook);

  return NULL;
}

SUBHOOK_EXPORT void SUBHOOK_API subhook_free(subhook_t hook) {
  if (hook == NULL) {
    return;
  }

  subhook_free_code(hook->trampoline, hook->trampoline_size);
  free(hook->code);
  free(hook);
}

SUBHOOK_EXPORT int SUBHOOK_API subhook_install(subhook_t hook) {
  int error;

  if (hook == NULL) {
    return -EINVAL;
  }
  if (hook->installed) {
    return -EINVAL;
  }

  error = subhook_make_jmp(hook->src, hook->dst, hook->flags);
  if (error >= 0) {
    hook->installed = true;
    return 0;
  }

  return error;
}

SUBHOOK_EXPORT int SUBHOOK_API subhook_remove(subhook_t hook) {
  if (hook == NULL) {
    return -EINVAL;
  }
  if (!hook->installed) {
    return -EINVAL;
  }

  memcpy(hook->src, hook->code, hook->jmp_size);
  hook->installed = 0;

  return 0;
}

SUBHOOK_EXPORT void *SUBHOOK_API subhook_read_dst(void *src) {
  struct subhook_jmp_arm64 *jmp = (struct subhook_jmp_arm64 *)src;

  if (jmp->ldr_x16 == 0x58000050 && jmp->br_x16 == 0xD61F0200) {
    return (void *)jmp->target;
  }

  return NULL;
}
