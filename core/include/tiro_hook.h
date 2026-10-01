/* Tiro's global hotkey for Windows: a low-level keyboard hook on its own native thread.
 *
 * The hook decides on its own thread whether to swallow each key (the dictation hotkey, Esc while dictating, the
 * "fix last transcription" shortcut), so it always answers Windows in microseconds, even while the app's Python
 * side is busy loading a model. Everything else is reported as events for the app to read with tiro_hook_wait().
 * Strings returned by the library must be released with tiro_hook_free().
 */
#ifndef TIRO_HOOK_H
#define TIRO_HOOK_H

#ifdef _WIN32
#ifdef TIRO_BUILDING_HOOK
#define TIRO_HOOK_API __declspec(dllexport)
#else
#define TIRO_HOOK_API __declspec(dllimport)
#endif
#else
#define TIRO_HOOK_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct tiro_hook tiro_hook;

/* Start the hook. config_json:
 *   {"hotkey": [[0xA3], ...],          one list of alternative virtual keys per key of the hotkey
 *    "mode": "hold" | "toggle", "double_tap_lock": true,
 *    "fix": [[0x11, 0xA2, 0xA3], [0x12, 0xA4, 0xA5], [0x46]] | null,   the "fix last transcription" shortcut
 *    "mark": 1414091359}              dwExtraInfo that marks Tiro's own typing (ignored by the hook)
 * Only one hook per process. Returns NULL on failure (with a message in *error to free with tiro_hook_free). */
TIRO_HOOK_API tiro_hook *tiro_hook_start(const char *config_json, char **error);
/* Change the hotkey / mode / fix shortcut (same JSON; "mark" is ignored). */
TIRO_HOOK_API int tiro_hook_configure(tiro_hook *hook, const char *config_json);
/* Has the current dictation heard speech yet? (A key pressed with the hotkey before any speech is a shortcut.) */
TIRO_HOOK_API void tiro_hook_set_speech(tiro_hook *hook, int heard);
/* Capture mode (recording a new hotkey in Settings): every key is swallowed and reported as a "capture" event. */
TIRO_HOOK_API void tiro_hook_set_capture(tiro_hook *hook, int on);
/* A dictation ended for another reason (error, menu): wait for the hotkey's release, then idle. */
TIRO_HOOK_API void tiro_hook_force_idle(tiro_hook *hook);
/* Re-register with Windows (cheap; done every 60 s while idle anyway). */
TIRO_HOOK_API void tiro_hook_reinstall(tiro_hook *hook);
/* The hotkey state machine's state (0 idle, 1 pressed, 2 tap pending, 3 lock held, 4 locked, 5 toggle held,
 * 6 toggled, 7 waiting for release, 8 chord). */
TIRO_HOOK_API int tiro_hook_state(tiro_hook *hook);
/* Wait up to timeout_ms for events. Returns 1 and a JSON array in *events_json, or 0 on timeout:
 *   {"action": "start"|"stop"|"cancel"|"lock"}  the dictation hotkey
 *   {"fire": "fix"}                              the fix-last shortcut
 *   {"key": [vk, scan, mods]}                    a key press that reached the app (mods: 1 ctrl, 2 shift, 4 alt, 8 win)
 *   {"capture": [vk, down]}                      capture mode
 *   {"reinstalled": true}                        the hook was registered again */
TIRO_HOOK_API int tiro_hook_wait(tiro_hook *hook, int timeout_ms, char **events_json);
TIRO_HOOK_API void tiro_hook_stop(tiro_hook *hook);
TIRO_HOOK_API void tiro_hook_free(void *ptr);

#ifdef __cplusplus
}
#endif

#endif /* TIRO_HOOK_H */
