#pragma once
#include "Arduino.h"
#include <map>
#include <vector>
#include <cstring>
class Preferences {
 std::string ns;
 using Bytes=std::vector<unsigned char>;
 public:
 static std::map<std::string,Bytes> data;
 static bool fail_blob;
 static bool fail_string;
 static bool fail_remove;
 bool begin(const char *name,bool=false){ns=name;return true;}
 void end(){}
 size_t putBytes(const char *key,const void *p,size_t n){if(fail_blob){fail_blob=false;return 0;}data[ns+"/"+key]=Bytes((const unsigned char *)p,(const unsigned char *)p+n);return n;}
 size_t getBytesLength(const char *key){return data[ns+"/"+key].size();}
 size_t getBytes(const char *key,void *out,size_t n){const auto &b=data[ns+"/"+key];if(b.size()!=n)return 0;memcpy(out,b.data(),n);return n;}
 size_t putString(const char *key,const char *value){if(fail_string){fail_string=false;return 0;}auto n=strlen(value);data[ns+"/"+key]=Bytes(value,value+n);return n+1;}
 String getString(const char *key,const char *fallback){auto it=data.find(ns+"/"+key);return it==data.end()?String(fallback):String(it->second.begin(),it->second.end());}
 size_t putInt(const char *k,int v){return putBytes(k,&v,sizeof(v));}
 int getInt(const char *k,int def){int v;return getBytes(k,&v,sizeof(v))?v:def;}
 size_t putBool(const char *k,bool v){return putBytes(k,&v,sizeof(v));}
 bool getBool(const char *k,bool def){bool v;return getBytes(k,&v,sizeof(v))?v:def;}
 bool remove(const char *k){if(fail_remove){fail_remove=false;return false;}return data.erase(ns+"/"+k)>0;}
};
