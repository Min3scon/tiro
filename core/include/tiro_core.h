/* Tiro speech engine: C API shared by every Tiro Lite app and the evaluation tools.
 *
 * Everything runs on the device. Audio is 16 kHz mono float32 PCM in [-1, 1]. Strings are UTF-8. Every string
 * the library returns must be released with tiro_free(). JSON is used for configuration and results so the
 * same API works from C, C++, Swift, Kotlin (JNI), JavaScript (WebAssembly) and Python (ctypes).
 */
#ifndef TIRO_CORE_H
#define TIRO_CORE_H

#include <stddef.h>
#include <stdint.h>

#if defined(TIRO_STATIC)
#define TIRO_API
#elif defined(_WIN32)
#ifdef TIRO_BUILDING_DLL
#define TIRO_API __declspec(dllexport)
#else
#define TIRO_API __declspec(dllimport)
#endif
#else
#define TIRO_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define TIRO_OK 0
#define TIRO_ERROR (-1)

typedef struct tiro_model tiro_model;

/* Version string of the library, e.g. "0.1.0". Static: do not free. */
TIRO_API const char *tiro_version(void);

/* Load a speech model from a folder. options_json may be NULL or e.g.
 *   {"threads": 2, "keyterms": ["GeoGuessr", "Siobhan"], "keyterm_boost": 2.0}
 * On failure returns NULL and, if error is not NULL, a message to free with tiro_free(). */
TIRO_API tiro_model *tiro_model_load(const char *model_dir, const char *options_json, char **error);
TIRO_API void tiro_model_free(tiro_model *model);

/* Transcribe a whole utterance at once (no streaming). Writes JSON to *result_json:
 *   {"text": "...", "tokens": n, "ms": total_ms, "encode_ms": .., "decode_ms": ..} */
TIRO_API int tiro_model_transcribe(tiro_model *model, const float *pcm, size_t samples, char **result_json);

/* The user's vocabulary: biases decoding towards the terms and lets the correction pass swap a misheard span for
 * one of them (never anything else). vocabulary_json:
 *   {"terms": ["GeoGuessr", "Siobhan"], "rules": [{"heard": "shiv on", "written": "Siobhan"}],
 *    "boost": 2.0, "common_words_file": "path/common-words.txt"}   (one word per line, most frequent first) */
TIRO_API int tiro_model_set_vocabulary(tiro_model *model, const char *vocabulary_json);

/* ---- Live dictation --------------------------------------------------------------------------------------
 * A session takes audio as it is captured and produces events. Voice-activity detection (the "vad" model given
 * to tiro_model_load) keeps silence away from the recogniser and ends a phrase at each pause.
 * options_json may be NULL or override SessionOptions, e.g. {"pause_ms": 600, "partial_ms": 480}. */
typedef struct tiro_session tiro_session;

TIRO_API tiro_session *tiro_session_begin(tiro_model *model, const char *options_json);
/* Feed 16 kHz mono float samples. Cheap work (VAD, streaming encoder) happens here; call it from a worker
 * thread, not the audio callback. */
TIRO_API int tiro_session_feed(tiro_session *session, const float *pcm, size_t samples);
/* Events since the last poll as a JSON array:
 *   [{"type": "partial"|"commit", "text": "...", "phrase": n, "audio_ms": .., "decode_ms": .., "latency_ms": ..}] */
TIRO_API int tiro_session_poll(tiro_session *session, char **events_json);
/* End of dictation: commits the phrase in progress. Writes {"text": all committed text, "phrases": n,
 * "speech_ms": .., "events": [... remaining events ...]}. */
TIRO_API int tiro_session_finish(tiro_session *session, char **result_json);
TIRO_API void tiro_session_free(tiro_session *session);

/* Peak and current resident memory of this process in bytes (0 if unknown). */
TIRO_API int64_t tiro_process_peak_rss(void);
TIRO_API int64_t tiro_process_rss(void);

TIRO_API void tiro_free(void *ptr);

#ifdef __cplusplus
}
#endif

#endif /* TIRO_CORE_H */
