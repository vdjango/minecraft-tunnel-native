// SPDX-License-Identifier: AGPL-3.0-or-later
#include "social.hpp"
#include <algorithm>
#include <chrono>
#include <mutex>
#include <openssl/ec.h>
#include <openssl/evp.h>
#include <openssl/objects.h>
#include <openssl/ecdsa.h>
#include <openssl/param_build.h>
#include <openssl/core_names.h>
namespace bridge {
namespace {
double social_timestamp() {
    static std::mutex clock_mutex;
    static double previous = 0;
    std::lock_guard<std::mutex> guard(clock_mutex);
    double current = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    previous = std::max(current, previous + 0.001);
    return previous;
}
std::string decode(std::string value){
    if(value.empty()||value.size()>8192||value.size()%4==1)throw Failure(-32602,"Invalid personal credential");
    for(auto& ch:value){if(ch=='-')ch='+';else if(ch=='_')ch='/';else if(!std::isalnum(static_cast<unsigned char>(ch))&&ch!='='&&ch!='+'&&ch!='/')throw Failure(-32602,"Invalid personal credential");}
    while(value.size()%4)value+='=';std::string bytes(value.size()/4*3,'\0');auto count=EVP_DecodeBlock(reinterpret_cast<unsigned char*>(bytes.data()),reinterpret_cast<const unsigned char*>(value.data()),static_cast<int>(value.size()));if(count<0)throw Failure(-32602,"Invalid personal credential");if(value.back()=='=')--count;if(value.size()>1&&value[value.size()-2]=='=')--count;bytes.resize(static_cast<size_t>(count));return bytes;
}
std::string encode(const std::string& bytes){std::string value(4*((bytes.size()+2)/3),'\0');EVP_EncodeBlock(reinterpret_cast<unsigned char*>(value.data()),reinterpret_cast<const unsigned char*>(bytes.data()),static_cast<int>(bytes.size()));for(auto& ch:value){if(ch=='+')ch='-';else if(ch=='/')ch='_';}while(!value.empty()&&value.back()=='=')value.pop_back();return value;}
}
Json social_event(const Profile& profile,const Json& params){
    auto split=profile.token.find('~');if(!profile.token.starts_with("acct1.")||split==std::string::npos)throw Failure(-32602,"A personal launcher credential is required");
    auto claims=Json::parse(decode(profile.token.substr(6,profile.token.find('.',6)-6)));
    auto now=std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    if(claims.at("e").get<int64_t>()<=now||!claims.at("g").contains("d"))throw Failure(-32602,"Personal credential expired; sign in on the node website");
    auto scalar=decode(profile.token.substr(split+1));if(scalar.size()!=32)throw Failure(-32602,"Invalid delegated key");
    auto private_key=std::unique_ptr<BIGNUM,decltype(&BN_clear_free)>(BN_bin2bn(reinterpret_cast<const unsigned char*>(scalar.data()),32,nullptr),BN_clear_free);OPENSSL_cleanse(scalar.data(),scalar.size());
    auto group=std::unique_ptr<EC_GROUP,decltype(&EC_GROUP_free)>(EC_GROUP_new_by_curve_name(NID_X9_62_prime256v1),EC_GROUP_free);
    auto point=std::unique_ptr<EC_POINT,decltype(&EC_POINT_free)>(EC_POINT_new(group.get()),EC_POINT_free);
    auto order=std::unique_ptr<BIGNUM,decltype(&BN_free)>(BN_new(),BN_free);
    if(!private_key||!group||!point||!order||!EC_GROUP_get_order(group.get(),order.get(),nullptr)||BN_is_zero(private_key.get())||BN_cmp(private_key.get(),order.get())>=0||!EC_POINT_mul(group.get(),point.get(),private_key.get(),nullptr,nullptr,nullptr))throw Failure(-32602,"Invalid delegated key");
    std::string public_key(65,'\0');if(EC_POINT_point2oct(group.get(),point.get(),POINT_CONVERSION_UNCOMPRESSED,reinterpret_cast<unsigned char*>(public_key.data()),public_key.size(),nullptr)!=65)throw Failure(-32602,"Delegated key unavailable");
    auto delegation=claims.at("g").at("d");if(decode(delegation.at("x").get<std::string>())!=public_key.substr(1,32)||decode(delegation.at("y").get<std::string>())!=public_key.substr(33))throw Failure(-32602,"Delegated key mismatch");
    auto builder=std::unique_ptr<OSSL_PARAM_BLD,decltype(&OSSL_PARAM_BLD_free)>(OSSL_PARAM_BLD_new(),OSSL_PARAM_BLD_free);
    OSSL_PARAM_BLD_push_utf8_string(builder.get(),OSSL_PKEY_PARAM_GROUP_NAME,"prime256v1",0);OSSL_PARAM_BLD_push_octet_string(builder.get(),OSSL_PKEY_PARAM_PUB_KEY,public_key.data(),public_key.size());OSSL_PARAM_BLD_push_BN(builder.get(),OSSL_PKEY_PARAM_PRIV_KEY,private_key.get());
    auto key_params=std::unique_ptr<OSSL_PARAM,decltype(&OSSL_PARAM_free)>(OSSL_PARAM_BLD_to_param(builder.get()),OSSL_PARAM_free);
    auto key_context=std::unique_ptr<EVP_PKEY_CTX,decltype(&EVP_PKEY_CTX_free)>(EVP_PKEY_CTX_new_from_name(nullptr,"EC",nullptr),EVP_PKEY_CTX_free);EVP_PKEY* raw_key=nullptr;
    if(!key_context||EVP_PKEY_fromdata_init(key_context.get())!=1||EVP_PKEY_fromdata(key_context.get(),&raw_key,EVP_PKEY_KEYPAIR,key_params.get())!=1)throw Failure(-32602,"Delegated key unavailable");
    auto key=std::unique_ptr<EVP_PKEY,decltype(&EVP_PKEY_free)>(raw_key,EVP_PKEY_free);
    Json action=params.value("fields",Json::object());if(!action.is_object())throw Failure(-32602,"Invalid social fields");
    action["op"]=params.at("operation");action["target"]=params.value("target",Json(nullptr));if(action["target"].is_null())action["target"]=claims.at("a");action["at"]=social_timestamp();action["id"]=room_id(random_id());
    auto bytes=action.dump(-1,' ',true);auto context=std::unique_ptr<EVP_MD_CTX,decltype(&EVP_MD_CTX_free)>(EVP_MD_CTX_new(),EVP_MD_CTX_free);size_t size=0;
    if(!context||EVP_DigestSignInit(context.get(),nullptr,EVP_sha256(),nullptr,key.get())!=1||EVP_DigestSign(context.get(),nullptr,&size,reinterpret_cast<const unsigned char*>(bytes.data()),bytes.size())!=1)throw Failure(-32602,"Social signature unavailable");
    std::string signature(size,'\0');if(EVP_DigestSign(context.get(),reinterpret_cast<unsigned char*>(signature.data()),&size,reinterpret_cast<const unsigned char*>(bytes.data()),bytes.size())!=1)throw Failure(-32602,"Social signature failed");
    const auto* input=reinterpret_cast<const unsigned char*>(signature.data());auto value=std::unique_ptr<ECDSA_SIG,decltype(&ECDSA_SIG_free)>(d2i_ECDSA_SIG(nullptr,&input,static_cast<long>(size)),ECDSA_SIG_free);if(!value)throw Failure(-32602,"Social signature failed");
    const BIGNUM *r,*s;ECDSA_SIG_get0(value.get(),&r,&s);std::string compact(64,'\0');if(BN_bn2binpad(r,reinterpret_cast<unsigned char*>(compact.data()),32)!=32||BN_bn2binpad(s,reinterpret_cast<unsigned char*>(compact.data()+32),32)!=32)throw Failure(-32602,"Social signature failed");
    return {{"grant",claims.at("g")},{"proof",claims.at("s")},{"action",action},{"signature",encode(compact)}};
}
}
