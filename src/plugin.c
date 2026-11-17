/* CPA C ABI v1 / schema v10. OpenAI chat -> Antigravity, opt-in only.
 * No request/session globals: the reserved bridge is carried in this request,
 * consumed after native translation, and never intentionally sent upstream.
 */
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <inttypes.h>
#include "cJSON.h"
#include "sha256.h"
#define HEADER "X-Pi-Cache-Session"
#define BRIDGE "__pi_cpa_cache_session_v1"
#define VERSION "0.1.0"
#define MAX_RPC (128u * 1024u * 1024u)

typedef struct { void *ptr; size_t len; } buffer;
typedef int (*host_call_fn)(void*,const char*,const uint8_t*,size_t,buffer*);
typedef void (*free_fn)(void*,size_t);
typedef struct { uint32_t abi_version; void *host_ctx; host_call_fn call; free_fn free_buffer; } host_api;
typedef struct { uint32_t abi_version; int (*call)(const char*,const uint8_t*,size_t,buffer*); free_fn free_buffer; void (*shutdown)(void); } plugin_api;
static const char b64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static const char *text(cJSON *obj,const char *key){cJSON *v=cJSON_GetObjectItemCaseSensitive(obj,key);return cJSON_IsString(v)?v->valuestring:"";}
static int output(buffer *out,const char *s){size_t n=strlen(s);out->ptr=malloc(n);if(!out->ptr)return 1;memcpy(out->ptr,s,n);out->len=n;return 0;}
static int empty(buffer *out){return output(out,"{\"ok\":true,\"result\":{}}");}
static int error(buffer *out,const char *message){
 cJSON *o=cJSON_CreateObject(),*e=cJSON_CreateObject();
 if(!o||!e){cJSON_Delete(o);cJSON_Delete(e);return 1;}
 cJSON_AddBoolToObject(o,"ok",0);cJSON_AddItemToObject(o,"error",e);
 cJSON_AddStringToObject(e,"code","pi_session_invalid");cJSON_AddStringToObject(e,"message",message);
 char *s=cJSON_PrintUnformatted(o);int rc=s?output(out,s):1;free(s);cJSON_Delete(o);return rc;
}
static char *encode(const char *s){
 size_t n=strlen(s),j=0;if(n>MAX_RPC)return NULL;
 char *out=calloc(1,4*((n+2)/3)+1);if(!out)return NULL;
 for(size_t i=0;i<n;i+=3){uint32_t v=(uint32_t)(unsigned char)s[i]<<16;if(i+1<n)v|=(uint32_t)(unsigned char)s[i+1]<<8;if(i+2<n)v|=(unsigned char)s[i+2];out[j++]=b64[(v>>18)&63];out[j++]=b64[(v>>12)&63];out[j++]=i+1<n?b64[(v>>6)&63]:'=';out[j++]=i+2<n?b64[v&63]:'=';}
 return out;
}
static char *decode(const char *s){
 size_t n=strlen(s);if(n>MAX_RPC || n%4)return NULL;
 char *out=calloc(1,n/4*3+1);if(!out)return NULL;size_t j=0;
 for(size_t i=0;i<n;i+=4){uint32_t v=0;int padding=0;
  for(int k=0;k<4;k++){char c=s[i+(size_t)k];if(c=='='){if(i+4!=n||k<2){free(out);return NULL;}padding++;v<<=6;}else{const char *p=strchr(b64,c);if(!p||padding){free(out);return NULL;}v=(v<<6)|(uint32_t)(p-b64);}}
  out[j++]=(char)(v>>16);if(padding<2)out[j++]=(char)(v>>8);if(!padding)out[j++]=(char)v;
 }
 // JSON bodies cannot contain literal NUL bytes (escaped \\u0000 is fine).
 if(memchr(out,0,j)){free(out);return NULL;}return out;
}
static cJSON *body(cJSON *req){
 cJSON *v=cJSON_GetObjectItemCaseSensitive(req,"Body");if(!cJSON_IsString(v))return NULL;
 char *raw=decode(v->valuestring);if(!raw)return NULL;
 cJSON *o=cJSON_ParseWithOpts(raw,NULL,1);free(raw);
 if(!cJSON_IsObject(o)){cJSON_Delete(o);return NULL;}return o;
}
static int body_output(buffer *out,cJSON *obj){
 char *raw=cJSON_PrintUnformatted(obj);char *encoded=raw?encode(raw):NULL;
 cJSON *o=cJSON_CreateObject(),*r=cJSON_CreateObject();int rc=1;
 if(encoded&&o&&r){cJSON_AddBoolToObject(o,"ok",1);cJSON_AddItemToObject(o,"result",r);r=NULL;cJSON_AddStringToObject(cJSON_GetObjectItemCaseSensitive(o,"result"),"Body",encoded);char *s=cJSON_PrintUnformatted(o);if(s){rc=output(out,s);free(s);}}
 free(raw);free(encoded);cJSON_Delete(r);cJSON_Delete(o);return rc;
}
static int valid_id(const char *s){
 size_t n=strlen(s);if(!n||n>128)return 0;
 for(size_t i=0;i<n;i++){unsigned char c=(unsigned char)s[i];if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='.'||c=='_'||c==':'||c=='-'))return 0;}return 1;
}
static const char *session_header(cJSON *req){
 cJSON *headers=cJSON_GetObjectItemCaseSensitive(req,"Headers"),*h=NULL;const char *found=NULL;
 cJSON_ArrayForEach(h,headers){if(h->string&&!strcasecmp(h->string,HEADER)){
  if(found||!cJSON_IsArray(h)||cJSON_GetArraySize(h)!=1)return "";
  cJSON *v=cJSON_GetArrayItem(h,0);if(!cJSON_IsString(v))return "";found=v->valuestring;
 }}return found;
}
static void upstream_id(const char *sid,char out[32]){
 // Versioned, domain-separated mapping; do not change without intentionally
 // invalidating all existing namespaces. A 63-bit ID has nonzero collision risk.
 static const char domain[]="pi-cpa-session-cache:v1:";
 char input[sizeof(domain)+128];snprintf(input,sizeof(input),"%s%s",domain,sid);
 uint8_t digest[32];session_sha256(input,strlen(input),digest);uint64_t n=0;
 for(int i=0;i<8;i++)n=(n<<8)|digest[i];
 n&=INT64_MAX;if(!n)n=1;
 snprintf(out,32,"-%" PRIu64,n);
}
static int valid_upstream_id(const char *s){size_t n=strlen(s);if(n<2||n>20||s[0]!='-'||s[1]=='0')return 0;for(size_t i=1;i<n;i++)if(s[i]<'0'||s[i]>'9')return 0;return 1;}
static int reject(buffer *out,const char *message){
 // Reject bad opted-in input before translation; do not silently use the parent cache.
 cJSON *o=cJSON_CreateObject(),*r=cJSON_CreateObject();if(!o||!r){cJSON_Delete(o);cJSON_Delete(r);return 1;}
 cJSON_AddBoolToObject(o,"ok",1);cJSON_AddItemToObject(o,"result",r);
 cJSON_AddBoolToObject(r,"Terminate",1);cJSON_AddNumberToObject(r,"StatusCode",400);
 char *encoded=encode(message);if(!encoded){cJSON_Delete(o);return 1;}cJSON_AddStringToObject(r,"ResponseBody",encoded);free(encoded);
 char *s=cJSON_PrintUnformatted(o);int rc=s?output(out,s):1;free(s);cJSON_Delete(o);return rc;
}
static int intercept(cJSON *req,buffer *out){
 if(strcmp(text(req,"ToFormat"),"antigravity"))return empty(out);
 const char *sid=session_header(req);if(!sid)return empty(out);
 if(strcmp(text(req,"SourceFormat"),"openai"))return reject(out,"pi-cpa-session-cache supports OpenAI Chat Completions only");
 if(!valid_id(sid))return reject(out,"Invalid X-Pi-Cache-Session; expected 1-128 ASCII identifier characters");
 cJSON *obj=body(req);if(!obj)return reject(out,"Invalid JSON body for opted-in cache session");
 cJSON *gc=cJSON_GetObjectItemCaseSensitive(obj,"generationConfig");
 if(!gc)gc=cJSON_GetObjectItemCaseSensitive(obj,"generation_config");
 if(gc&&!cJSON_IsObject(gc)){cJSON_Delete(obj);return reject(out,"generationConfig must be an object");}
 if(gc&&cJSON_GetObjectItemCaseSensitive(gc,BRIDGE)){cJSON_Delete(obj);return reject(out,"Reserved cache-session bridge field already exists");}
 if(!gc){gc=cJSON_CreateObject();if(!gc||!cJSON_AddItemToObject(obj,"generationConfig",gc)){cJSON_Delete(gc);cJSON_Delete(obj);return 1;}}
 char id[32];upstream_id(sid,id);
 if(!cJSON_AddStringToObject(gc,BRIDGE,id)){cJSON_Delete(obj);return 1;}
 int rc=body_output(out,obj);cJSON_Delete(obj);return rc;
}
static int normalize(cJSON *req,buffer *out){
 if(strcmp(text(req,"ToFormat"),"antigravity")||strcmp(text(req,"FromFormat"),"openai"))return empty(out);
 cJSON *obj=body(req);if(!obj)return empty(out);
 cJSON *r=cJSON_GetObjectItemCaseSensitive(obj,"request"),*gc=cJSON_GetObjectItemCaseSensitive(r,"generationConfig");
 cJSON *marker=cJSON_GetObjectItemCaseSensitive(gc,BRIDGE);
 if(!marker){cJSON_Delete(obj);return empty(out);}
 if(!cJSON_IsObject(r)||!cJSON_IsString(marker)||!valid_upstream_id(marker->valuestring)){cJSON_Delete(obj);return error(out,"Invalid reserved cache-session bridge");}
 cJSON *id=cJSON_CreateString(marker->valuestring);if(!id){cJSON_Delete(obj);return 1;}
 cJSON_DeleteItemFromObjectCaseSensitive(r,"sessionId");
 if(!cJSON_AddItemToObject(r,"sessionId",id)){cJSON_Delete(id);cJSON_Delete(obj);return 1;}
 cJSON_DeleteItemFromObjectCaseSensitive(gc,BRIDGE);
 // Preserve an existing empty generation config too; never drop user options.
 int rc=body_output(out,obj);cJSON_Delete(obj);return rc;
}
static int plugin_call(const char *method,const uint8_t *request,size_t len,buffer *out){
 if(!out)return 1;
 out->ptr=NULL;out->len=0;if(!method)return error(out,"Missing method");
 if(!strcmp(method,"plugin.register")||!strcmp(method,"plugin.reconfigure"))return output(out,
 "{\"ok\":true,\"result\":{\"schema_version\":10,\"metadata\":{\"Name\":\"pi-cpa-session-cache\",\"Version\":\"" VERSION "\",\"Author\":\"ping1999\",\"GitHubRepository\":\"https://github.com/ping1999/cpa-pi-session-cache\",\"ConfigFields\":[]},\"capabilities\":{\"request_interceptor\":true,\"request_normalizer\":true,\"request_interceptor_omit_body_before\":true}}}");
 if(!strcmp(method,"plugin.quiesce")||!strcmp(method,"plugin.shutdown")||!strcmp(method,"request.intercept_before"))return empty(out);
 if(strcmp(method,"request.intercept_after")&&strcmp(method,"request.normalize"))return empty(out);
 if(!request||!len||len>MAX_RPC)return error(out,"Invalid RPC size");
 cJSON *req=cJSON_ParseWithLengthOpts((const char*)request,len,NULL,0);
 if(!cJSON_IsObject(req)){cJSON_Delete(req);return error(out,"Invalid RPC JSON");}
 int rc=!strcmp(method,"request.intercept_after")?intercept(req,out):normalize(req,out);cJSON_Delete(req);return rc;
}
static void plugin_free(void *p,size_t len){(void)len;free(p);}
static void plugin_shutdown(void){}
#if defined(_WIN32)
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
int cliproxy_plugin_init(const host_api *host,plugin_api *plugin){
 if(!host||host->abi_version!=1||!plugin)return 1;
 plugin->abi_version=1;plugin->call=plugin_call;plugin->free_buffer=plugin_free;plugin->shutdown=plugin_shutdown;return 0;
}
