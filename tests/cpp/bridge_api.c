/* A real C consumer: no C++ runtime types or allocator cross the ABI. */
#include <buggcraft/bridge.h>
#include <stdio.h>
#include <string.h>
#define CHECK(value) do { if(!(value)){fprintf(stderr,"ABI check failed at line %d\n",__LINE__);return 1;} } while(0)
int main(void){
    buggcraft_bridge *first=NULL,*second=NULL;
    char* response=NULL;size_t size=0;
    const char* request="{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"bridge.initialize\"}";
    CHECK(buggcraft_bridge_abi_version()==1);
    CHECK(buggcraft_bridge_create(NULL)==BUGGCRAFT_INVALID_ARGUMENT);
    CHECK(buggcraft_bridge_create(&first)==BUGGCRAFT_OK);
    CHECK(buggcraft_bridge_create(&second)==BUGGCRAFT_OK);
    CHECK(buggcraft_bridge_call(NULL,request,strlen(request),&response,&size)==BUGGCRAFT_INVALID_ARGUMENT);
    CHECK(buggcraft_bridge_call(first,request,8193,&response,&size)==BUGGCRAFT_INVALID_ARGUMENT);
    CHECK(response==NULL && size==0);
    CHECK(buggcraft_bridge_call(first,request,strlen(request),&response,&size)==BUGGCRAFT_OK);
    CHECK(response && strlen(response)==size && strstr(response,"\"implementation\":\"cpp\"") && strstr(response,"\"id\":7"));
    buggcraft_bridge_free(response);
    request="bad json";
    CHECK(buggcraft_bridge_call(second,request,strlen(request),&response,&size)==BUGGCRAFT_OK);
    CHECK(strstr(response,"-32700"));buggcraft_bridge_free(response);
    request="{\"jsonrpc\":\"2.0\",\"method\":\"session.list\"}";
    CHECK(buggcraft_bridge_call(first,request,strlen(request),&response,&size)==BUGGCRAFT_OK);
    CHECK(response==NULL && size==0);
    buggcraft_bridge_destroy(first);
    request="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"session.list\"}";
    CHECK(buggcraft_bridge_call(second,request,strlen(request),&response,&size)==BUGGCRAFT_OK);
    CHECK(strstr(response,"\"sessions\":[]"));buggcraft_bridge_free(response);
    buggcraft_bridge_destroy(second);
    CHECK(buggcraft_bridge_create(&first)==BUGGCRAFT_OK);
    buggcraft_bridge_destroy(first);buggcraft_bridge_destroy(NULL);buggcraft_bridge_free(NULL);
    return 0;
}
