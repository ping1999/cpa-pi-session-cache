# Vendored dependency

`cjson/cJSON.c` and `cjson/cJSON.h`: cJSON v1.7.19, https://github.com/DaveGamble/cJSON/tree/v1.7.19 . MIT license retained in both source headers.

Local change: `global_error` is `_Thread_local` rather than shared static state, because CPA can call the plugin concurrently. No global allocator hooks are installed or modified.
