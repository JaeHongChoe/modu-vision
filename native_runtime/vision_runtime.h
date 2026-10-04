#pragma once
#ifdef _WIN32
#define MV_API __declspec(dllexport)
#else
#define MV_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* UTF-8 strings. Returned buffers belong to this library; free with mv_free. */
MV_API void* mv_create(const char* package_dir, const char* options_json, char** error);
MV_API int mv_execute(void* handle, const char* input_json, char** output_json);
MV_API int mv_predict(void* handle, const char* image_path, const char* image_id, char** output_json);
/* 1: requested for active call, 0: idle, -1: invalid/unsupported handle.
 * Execute/predict statuses: 0 result, 1 error, 2 timeout, 3 cancelled.
 * Retain handle until all execute/cancel calls have returned. */
MV_API int mv_cancel(void* handle);
MV_API void mv_release(void* handle);
MV_API void mv_free(char* text);
#ifdef __cplusplus
}
#endif
