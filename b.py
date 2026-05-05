#!/usr/bin/env python3
"""
apply_log_suppress.py
Patch logger_write.cpp dengan:
  - Hardcoded defaults (selalu aktif, pthread_once cached)
  - Runtime rules via system property persist.log.suppress.N

Tidak ada include tambahan — sys/system_properties.h dan pthread.h
sudah ada di logger_write.cpp.

Usage:
    python3 apply_log_suppress.py <path/to/logger_write.cpp>

Set rule:
    adb shell setprop persist.log.suppress.0 "msg:== MALI DEBUG ===..."
    adb shell setprop persist.log.suppress.1 "tag:SomeTag"
    adb reboot

Idempotent: aman dijalankan ulang.
"""

import sys

# -- Hardcoded default rules ---------------------------------------------------
# Selalu aktif, tidak perlu property.
#
# "type": "tag"
#     "tag"          : exact match pada tag
#     "max_priority" : suppress hanya jika priority <= nilai (opsional)
#
# "type": "message"
#     "message"       : string yang dicocokkan
#     "match"         : "exact" (strcmp) atau "substring" (strstr)
#     "only_priority" : pre-filter priority sebelum string check (opsional)
#
# Android priority: VERBOSE=2 DEBUG=3 INFO=4 WARN=5 ERROR=6 FATAL=7
# -----------------------------------------------------------------------------
HARDCODED_RULES = [
    {
        "type": "tag",
        "tag": "Choreographer",
        "max_priority": "ANDROID_LOG_WARN",
    },
    {
        "type": "tag",
        "tag": "libperfmgr",
    },
    {
        "type": "message",
        "match": "exact",
        "message": "== MALI DEBUG ===eglp_winsys_populate_image_templates ==12288",
        "only_priority": "ANDROID_LOG_ERROR",
    },
]

# -- C loader + checker --------------------------------------------------------
# Disisipkan sebelum void __android_log_write_log_message().
#
# Keputusan desain:
#   - sys/system_properties.h : sudah di-include oleh logger_write.cpp (baris 62)
#   - pthread.h               : sudah di-include oleh logger_write.cpp (baris 53)
#   - Tidak ada include baru  : nol risiko konflik
#   - Semua kode di dalam #ifdef __ANDROID__ : host build (linux_glibc) tidak kena
#   - Static POD only         : tidak ada exit-time destructor (-Wexit-time-destructors)
#   - Semua switch case punya break : tidak ada implicit fallthrough (-Wimplicit-fallthrough)
#   - Semua static function dipanggil : tidak ada unused-function (-Wunused-function)
#   - Semua variable di-init  : tidak ada uninitialized warning
# -----------------------------------------------------------------------------
LOADER_CODE = r"""
#ifdef __ANDROID__
// -- Log suppression: property-based runtime config ---------------------------
// Rules dibaca dari persist.log.suppress.0 .. persist.log.suppress.15
// sekali per-process via pthread_once. Ubah property -> reboot untuk efektif.
//
// Format value property:
//   msg:<exact message>  -> strcmp exact
//   sub:<substring>      -> strstr
//   tag:<tag name>       -> suppress semua log dari tag ini
// -----------------------------------------------------------------------------
#define SUPPRESS_PROP_PREFIX  "persist.log.suppress."
#define SUPPRESS_PROP_MAX_IDX 16
#define SUPPRESS_PROP_VAL_MAX PROP_VALUE_MAX

enum SuppressRuleType { SUPPRESS_MSG_EXACT, SUPPRESS_MSG_SUB, SUPPRESS_TAG };

struct SuppressRule {
    SuppressRuleType type;
    char value[SUPPRESS_PROP_VAL_MAX];
};

static SuppressRule   s_suppress_rules[SUPPRESS_PROP_MAX_IDX];
static int            s_suppress_count = 0;
static pthread_once_t s_suppress_once  = PTHREAD_ONCE_INIT;

static void SuppressAddRule(SuppressRuleType type, const char* val) {
    if (s_suppress_count >= SUPPRESS_PROP_MAX_IDX) return;
    s_suppress_rules[s_suppress_count].type = type;
    strncpy(s_suppress_rules[s_suppress_count].value, val, SUPPRESS_PROP_VAL_MAX - 1);
    s_suppress_rules[s_suppress_count].value[SUPPRESS_PROP_VAL_MAX - 1] = '\0';
    s_suppress_count++;
}

static void LoadSuppressConfig() {
    // Build property names "persist.log.suppress.0" .. "persist.log.suppress.15"
    // menggunakan prefix + manual int-to-char, tanpa snprintf/stdio dependency.
    static const char kPrefix[] = SUPPRESS_PROP_PREFIX;
    const int kPrefixLen = static_cast<int>(sizeof(kPrefix)) - 1;

    for (int i = 0; i < SUPPRESS_PROP_MAX_IDX; i++) {
        char prop_name[64];
        strncpy(prop_name, kPrefix, sizeof(prop_name) - 1);
        prop_name[sizeof(prop_name) - 1] = '\0';
        // Append index as string (0..15, max 2 digits)
        int pos = kPrefixLen;
        if (i >= 10) {
            prop_name[pos++] = static_cast<char>('0' + i / 10);
        }
        prop_name[pos++] = static_cast<char>('0' + i % 10);
        prop_name[pos]   = '\0';

        char prop_val[SUPPRESS_PROP_VAL_MAX] = {};
        if (__system_property_get(prop_name, prop_val) == 0) break;  // tidak ada, stop

        SuppressRuleType type = SUPPRESS_MSG_EXACT;  // init to silence -Wuninitialized
        const char* val       = nullptr;             // always assigned before use

        if      (strncmp(prop_val, "msg:", 4) == 0) { type = SUPPRESS_MSG_EXACT; val = prop_val + 4; }
        else if (strncmp(prop_val, "sub:", 4) == 0) { type = SUPPRESS_MSG_SUB;   val = prop_val + 4; }
        else if (strncmp(prop_val, "tag:", 4) == 0) { type = SUPPRESS_TAG;       val = prop_val + 4; }
        else continue;  // prefix tidak dikenal, skip

        if (val[0] == '\0') continue;  // nilai kosong, skip
        SuppressAddRule(type, val);
    }
}

static bool IsSuppressedByConfig(const __android_log_message* log_message) {
    pthread_once(&s_suppress_once, LoadSuppressConfig);
    if (s_suppress_count == 0) return false;

    const char* tag = log_message->tag;
    const char* msg = log_message->message;

    for (int i = 0; i < s_suppress_count; i++) {
        const SuppressRule& r = s_suppress_rules[i];
        switch (r.type) {
            case SUPPRESS_TAG:
                if (tag != nullptr && strcmp(tag, r.value) == 0) return true;
                break;
            case SUPPRESS_MSG_EXACT:
                if (msg != nullptr && strcmp(msg, r.value) == 0) return true;
                break;
            case SUPPRESS_MSG_SUB:
                if (msg != nullptr && strstr(msg, r.value) != nullptr) return true;
                break;
        }
    }
    return false;
}
#endif  // __ANDROID__
// -- End log suppression loader -----------------------------------------------
"""


# -- C check block di dalam fungsi ---------------------------------------------
def build_check_block(rules):
    tag_conds, msg_conds = [], []

    for r in rules:
        if r["type"] == "tag":
            cond = 'strcmp(tag, "{}") == 0'.format(r["tag"])
            if "max_priority" in r:
                cond = '({} && log_message->priority <= {})'.format(cond, r["max_priority"])
            else:
                cond = '({})'.format(cond)
            tag_conds.append(cond)
        elif r["type"] == "message":
            msg_val  = r["message"]
            null_chk = 'log_message->message != nullptr'
            str_chk  = 'strcmp(log_message->message, "{}") == 0'.format(msg_val) \
                       if r.get("match") == "exact" \
                       else 'strstr(log_message->message, "{}") != nullptr'.format(msg_val)
            if "only_priority" in r:
                cond = '(log_message->priority == {} && {} && {})'.format(
                    r["only_priority"], null_chk, str_chk)
            else:
                cond = '({} && {})'.format(null_chk, str_chk)
            msg_conds.append(cond)

    lines = [
        '#ifdef __ANDROID__',
        '  // -- Log suppression: hardcoded defaults --------------------------------',
    ]
    if tag_conds:
        lines += [
            '  if (log_message->tag != nullptr) {',
            '    const char* tag = log_message->tag;',
            '    if (' + ' ||\n        '.join(tag_conds) + ') return;',
            '  }',
        ]
    if msg_conds:
        lines.append('  if (' + ' ||\n      '.join(msg_conds) + ') return;')
    lines += [
        '  // -- Log suppression: property-based config ------------------------------',
        '  if (IsSuppressedByConfig(log_message)) return;',
        '#endif  // __ANDROID__',
    ]
    return '\n'.join(lines)


# -- Anchors -------------------------------------------------------------------
# Loader disisipkan tepat sebelum definisi fungsi
FUNC_ANCHOR = 'void __android_log_write_log_message(__android_log_message* log_message) {'

# Check block disisipkan setelah null-check tag di dalam fungsi
CHECK_ANCHOR = '  if (log_message->tag == nullptr) {\n    log_message->tag = GetDefaultTag().c_str();\n  }'

# Guard idempotency
GUARD = '// -- Log suppression: hardcoded defaults'


def patch(filepath, rules):
    with open(filepath, 'r', encoding='utf-8') as f:
        src = f.read()

    if GUARD in src:
        print("[INFO] Sudah di-patch. Tidak ada perubahan.")
        return

    for name, anchor in [("function", FUNC_ANCHOR), ("check", CHECK_ANCHOR)]:
        if anchor not in src:
            print("[ERROR] Anchor '{}' tidak ditemukan.".format(name))
            sys.exit(1)

    # 1. Sisipkan loader sebelum fungsi
    src = src.replace(FUNC_ANCHOR, LOADER_CODE + '\n' + FUNC_ANCHOR, 1)

    # 2. Sisipkan check block di dalam fungsi
    src = src.replace(CHECK_ANCHOR, CHECK_ANCHOR + '\n\n' + build_check_block(rules), 1)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(src)

    print("[OK] Patch berhasil diterapkan ke {}".format(filepath))
    print("     Hardcoded rules : {}".format(len(rules)))
    print("     Runtime config  : persist.log.suppress.0 .. persist.log.suppress.15")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python3 {} <path/to/logger_write.cpp>".format(sys.argv[0]))
        sys.exit(1)
    patch(sys.argv[1], HARDCODED_RULES)
